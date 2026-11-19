# ADS IOC resilience profile: PLC reactivation and network events

Date: 2026-11-17. Status: PLC reactivation (5 runs), cable pull (2 runs) and a network stall ladder through a proxy (29 trials) profiled. The stuck bulk loop is reproduced, explained and fixed in a test build. A second failure (AdsLib stream desync, F12) appeared in the F10 build, was reproduced on demand, and is fixed in a second test patch.
Feeds: Phase 3 of `twincat-ads_update_plan.md`. Earlier observation: `ads-issue-observation.txt`.

## Summary

Two production symptoms were reported: the IOC needs 2-3 restarts after a PLC config reactivation, and after a network transient the bulk read loop keeps printing `Sum read N failed`. Profiling the sample IOC against the live PLC found three IOC exit paths during a reactivation. A network stall longer than the ADS timeout (6 s or more), with the TCP session kept open, reproduced the stuck bulk loop in 5 of 6 trials. A one-line fix (F10) removed it in 7 of 8 trials. The eighth trial exposed an AdsLib bug (F12) that leaves the connection unusable until the IOC exits. A mid-frame stall reproduced it in 4 of 5 trials on demand, and the F12 patch removed it in 5 of 5. The repeated restarts after reactivation were not reproduced.

1. **`exit(-1)` after two failed state polls.** The IOC exits 0.6 s to 11 s after the PLC leaves RUN. Confirmed in four runs (exit code 255).
2. **Silent SIGPIPE death.** If the PLC closes the TCP connection while the IOC is booting, the IOC is killed by SIGPIPE with no log line (exit code 141). Confirmed once.
3. **Slow detection.** On a hard outage the state poll blocks for 5.5 s, not the configured 1 s, so exit takes 6 to 11 s.

Each exit hands recovery to procServ, and the IOC takes about 80 s to serve again (6 s to exit, 15 s holdoff, 2 s to resolve handles, about 58 s of record init before the first state poll). Every restart in these runs landed after the PLC was back, so handle resolution succeeded at the first restart.

## Test bed

| Item | Value |
|---|---|
| PLC | 192.168.0.9 (AMS 192.168.0.9.1.1, port 851), config activated by hand in XAE |
| IOC | `lcls-plc-motion-abstraction-sample`, rebuilt with pytmc 2.22.1 and prefix `PLC:TST` |
| Driver | site `twincat-ads` R2.0.0-1.1.1, copied to `profiling/ads-1.1.1-prof` with log-only `PROF` lines, linked into a scratch ads-ioc R2.2.1 |
| Load | 7363 symbols, about 7200 bulk params, `ADS_TIMEOUT_MS=1000`, sample 50 ms |
| Supervisor | procServ emulated by `profiling/procserv_sim.sh` (holdoff 15 s, 3 s) |

Two test-bed problems were fixed before profiling. pytmc 2.21.1 could not parse the array pragma `1..3 | 1..3`, so a 2.22.1 venv was used. The old prefix `PLC:motion_abstraction_sample:` made long-named records fail to create, which left the IOC with 48 symbols. Prefix `PLC:TST:` loads 8907 records with no errors.

## Method

`profiling/run_scenario.sh` starts the IOC with raw, unfiltered logging and wall-clock stamps. `watch_pvs.py` logs PV updates. `analyze.py` builds a timeline from the log. The instrumented driver logs state reads, invalidate and refresh calls, handle-resolve results, bulk failures and `exit(-1)`. The wrapper records the IOC's own exit status. See `profiling/README.md` for commands.

## Results

| Run | Scenario | PLC event to IOC exit | Exit code | Restart outcome |
|---|---|---|---|---|
| A1 | IOC running, PLC to CONFIG mode | 0.57 s | 255 | not restarted |
| A2 | IOC running, activate (48 symbols) | 11 s | 0 (wrapper rc) | restart 1 resolved 48/48 |
| A3 | IOC running, activate | 6 s | 0 (wrapper rc) | restart 1 resolved 7363/7363 |
| A2b | activate, holdoff 3 s | 6 s | 0 (wrapper rc) | restart 1 resolved 7363/7363 |
| A4c | activate, IOC polling | 6 s | 255 | not restarted |
| A4d | activate while IOC boots | 9 s after start | **141** | restart 1 resolved 7363/7363 |
| B1a | cable out about 50 s (IOC polling) | 46 s | 255 | restart 1 resolved 7363/7363 |
| B1b | cable pulled and replugged quickly (IOC polling) | 0.6 s | 255 | restart 1 started |

Exit codes for A2 to A3 were not captured (wrapper added later). The exit path is the same `EXIT_NEG1` log line in each.

### Timeline: IOC running, PLC reactivated (A3)

| Time | Event |
|---|---|
| 15:10:01.77 | State read on port 851 fails (err 4), `CONN_LOST`, `INVALIDATE` (bulk readSize 62499) |
| 15:10:07.32 | Poll 5.5 s later fails (err 1861 SYNCTIMEOUT), `notConn=2`, `exit(-1)` |
| 15:10:23.43 | procServ restart after 15 s holdoff |
| 15:10:25.20 | Symbol info and handles 7363/7363 |
| 15:11:21.16 | First state poll, port 851 RUN |

### Timeline: PLC reactivated while the IOC boots (A4d, cycle 1)

| Time | Event |
|---|---|
| 15:29:33.7 | IOC start |
| 15:29:36.0 | Handles 7363/7363 |
| 15:29:36.7 | 133 per-symbol lookups fail with `ADSERR_DEVICE_SYMBOLNOTFOUND` (0x710) during record init |
| 15:29:37.92 | AdsLib: "connection closed by remote" |
| 15:29:42.83 | IOC killed by SIGPIPE, no message (exit code 141) |
| 15:29:45.8 | Restart: handles 7363/7363, no errors |

### Timeline: quick cable pull while the IOC polls (B1b)

| Time | Event |
|---|---|
| 15:42:07.47 | All 19 bulk groups fail at once with status -1 (the socket is gone, not a timeout) |
| 15:42:07.85 | State reads on ports 851, 350 to 353 fail instantly (err -1), `CONN_LOST`, `INVALIDATE`, `notConn=1` |
| 15:42:08.40 | Second round: "write frame failed with error: Broken pipe" on all five ports, no reconnect attempt, `notConn=2` |
| 15:42:08.46 | `exit(-1)` (exit code 255), 0.6 s after the first failure |

A cable pull breaks the TCP session. The IOC does not reopen it between polls, so the second round fails on the dead socket and the IOC exits. A blip that breaks TCP therefore never reaches the bulk-recovery code.

### Timeline: 8 s network stall, TCP kept open (C2)

The IOC connected through `ads_stall_proxy.py`, which held traffic in both directions for 8 s and then released it.

| Time | Event |
|---|---|
| 15:59:51.33 | Stall starts (proxy log) |
| 15:59:56.38 | First bulk read times out (status 1861), 5 s after the stall began |
| 15:59:56.56 | State poll fails, `CONN_LOST`, `INVALIDATE`: `bulkOK=0`, every bulk group's `readSize` set to 16 |
| 15:59:59.33 | Stall ends. Late replies arrive: "InvokeId mismatch", "No response pending" |
| 15:59:59.38 | Cyclic thread sets `connectedAds_=0` (no port answered in that round), `notConn=1` |
| 15:59:59.89 | Next state poll succeeds (RUN). `notConn` resets, so the IOC does not exit. `refreshParams` runs with `connectedAds=0`, skips its body, and sets `bulkOK=1` |
| 16:00:00.38 on | All 19 bulk groups fail every cycle with status 1797 (`ADSERR_DEVICE_INVALIDSIZE`, "parameter size not correct"), `readSize=16` for 500 entries |
| 16:00:39 | Still failing: 600 failures in 43 s, `refreshParams` called again every 0.5 s, PVs `COMM INVALID` / `LINK INVALID`. The IOC never recovers and never exits. |

The loop needs a restart to clear.

## Stall ladder (proxy holds traffic, TCP stays open)

Each trial uses a fresh IOC and a fresh proxy, one stall, and a 60 s observation. Baseline is the shipped 1.1.1 driver with log lines only. Fix is the same driver plus the F10 patch (`profiling/fix-F10-connectedAds.patch`).

| Stall | Baseline | With F10 fix |
|---|---|---|
| 1 s, 2 s, 3 s, 4 s, 5 s | 15 of 15 no effect | not run |
| 6 s | 2 stuck, 1 recovered | 4 recovered |
| 8 s | 3 stuck | 3 recovered, 1 exit (F12) |

Stalls up to 5 s are absorbed by TCP and the 5 s ADS timeout. From 6 s the first bulk read times out about 5.2 s after the stall starts. The one baseline trial that recovered started its stall after the 851 state poll had already succeeded in that round, so the state poll that timed out was a task port, `notConn` stayed 0, and `connectedAds_` was never cleared. The stuck loop needs the 851 poll itself to fail inside the stall.

## Mid-frame stall trials (F12)

The proxy's `split` mode delivers about 80 to 200 bytes of a PLC response, which is past the AMS and AoE headers and inside the payload, then holds the rest for 8 s. The desync column counts "Frame to short to be AoE" and "Port ... out of range" warnings.

| Build | Trials | Result |
|---|---|---|
| F10 only | 5 | 4 `exit(-1)` after 51 to 52 s (desync warnings 339, 699, 343, 3), 1 self-healed (3 desync warnings) |
| F10 + F12 | 5 | 5 recovered, 0 desync warnings, 1 `ReceiveFrame timeout: N bytes already consumed, skipping M` line each |

A first split run that cut inside the 46-byte AMS/AoE headers had no effect, because AdsLib reads headers without a deadline. Only a cut inside the payload, which is read against the request's 5 s deadline, triggers F12.

Patches: `profiling/fix-F10-connectedAds.patch` (driver) and `profiling/fix-F12-receiveframe.patch` (`AmsConnection.cpp` and `.h`). Builds: `ads-ioc-fix` (F10) and `ads-ioc-fix12` (F10 + F12).

## Second system: PLC 172.21.148.154 (2026-11-17, proxy faults only)

The harness was carried to a second host (IOC host 134.79.165.33, PLC 172.21.148.154, RTT about 0.4 ms, owned by this project, no other users). The faults were injected with `ads_stall_proxy.py` only, so the network itself was not disturbed and Phase 1 (baseline on the real network) is still open. Builds `base`, `f10`, `f12` were made from the site module R2.0.0-1.1.1 and the site ads-ioc R2.2.0 (R2.2.1 is not installed on this host). The sample IOC loaded 7352 symbols (7363 on the bench PLC), `ADS_TIMEOUT_MS=1000`. The state poll started 17 s after IOC start, so ladder trials used `FIRST=40` and `OBS=60`. Evidence: `evidence/second-system/` (trimmed with `trim-evidence.sh`).

| Fault | Build | Trials | Outcome |
|---|---|---|---|
| Stall 1 s, 2 s | f12 | 3 each | 6 NOEFFECT |
| Stall 3 s | f12 | 2 | 2 NOEFFECT |
| Stall 4 s | base | 3 | 3 NOEFFECT |
| Stall 5 s | base | 10 | 10 RECOVERED, exactly 1 bulk failure each |
| Stall 5 s | f12 | 10 | 10 RECOVERED, exactly 1 bulk failure each |
| Stall 6 s | base | 3 | 3 STUCK (915 status-1797 failures each) |
| Stall 6 s | f12 | 10 | 10 RECOVERED |
| Stall 8 s | base | 3 | 3 STUCK |
| Stall 8 s | f12 | 3 | 3 RECOVERED |
| Stall 12 s | base | 3 | 3 STUCK |
| Stall 12 s | f12 | 3 | 3 RECOVERED |
| Mid-frame stall 8 s (`split`) | f10 | 3 | 3 EXIT 255 after about 43 s, 327 to 335 desync warnings |
| Mid-frame stall 8 s (`split`) | f12 | 3 | 3 RECOVERED, 0 desync, one `bytes already consumed` line each |
| Socket cut (`cut`) | base | 3 | 3 EXIT 255 |
| Socket cut (`cut`) | f12 | 3 | 3 EXIT 255 |

Observations:

- F10 reproduces here in 9 of 9 base trials at 6 s or more (bench: 5 of 6). F12 reproduces in 3 of 3 f10 trials (bench: 4 of 5). Both are fixed in every f12 trial (16 of 16 pause trials at 6 s or more, 3 of 3 split trials).
- The stall threshold is 5 s here. A 5 s stall makes exactly one bulk read time out, and the IOC recovers without a patch, so the bench result of "no effect up to 5 s" does not hold on this PLC. A stall of 6 s or more leaves the base IOC stuck.
- A cut reproduces F1/F9: the first state poll fails, the second 0.54 s later fails on the dead socket, and `exit(-1)` follows (exit code 255, `EXIT notConnectedCounter=2`). The F10 and F12 patches do not change this, so F1 still needs its own fix.
- After the `exit(-1)` log line the process lives about 7.7 s more (`IOC_RC` at +8.7 s after the cut, `EXIT` at +0.5 s). The bulk thread is blocked on a 5 s read timeout and the process waits for it. Not investigated further.
- `ads_stall_proxy.py` in `cut` mode now logs `STALL_START` and `STALL_END`, so `classify.py` and `ladder.sh` handle it like a stall of length 0.

Not done on this system: stalls of 1 to 3 s on base and 4 s on f12, 10 or more trials at 8 s and 12 s, `tc`/`nft` faults, boot-time faults, and the F13 measurement.

### PLC reactivation on the second system (2026-11-18)

A TwinCAT reactivation in XAE is two steps: "Activate Configuration" (uploads `CurrentConfig.tzip`, `Plc\Port_851.app` and registry entries over the system service, port 10000) and, if XAE asks, "Restart TwinCAT System in Run Mode". An ADS capture of XAE against this PLC (port 10000 only) showed the restart is a single **Write Control to port 10000, AdsState 2 (RESET), DevState 0, no data**, answered with error 0 at once. With dialog 1 only (the PLC went to STOP and came back), the shipped driver exited with `exit(-1)` every time. Runs `react-A1`, `react-B1`, `inj-C1` and the user's own IOC log:

| Event | IOC outcome |
|---|---|
| Dialog 1 only, 8 events (`react-A1` 5, `react-B1` 3; IOC polling) and 1 injected Reset (`inj-C1`) | 9 of 9 `exit(-1)` (code 255) 0.5 to 0.6 s after port 851 reported STOP (state 6), never a crash. One event (`react-B1` 14:32:50) had a 5.5 s PLC silence with the TCP session open before the exit. |
| Dialog 1 then dialog 2, IOC booted between, shipped driver (user's `ioc.log`, 13:49 to 13:53) | First IOC: `exit(-1)` followed by **SIGSEGV** (core dumped) while the bulk thread still ran. Second IOC: connection lost and back in 0.52 s, no exit, then `Sum read N failed: status 1797` on every group, still failing at the end of the log (746 lines). This is F10 from a real reactivation. |

`ads_stall_proxy.py --inject-reset` sends the same Write Control on the IOC's own connection and AMS Net ID (no extra route), swallows the reply, and with `--probe-s` polls port 851 every 100 ms. Three trials (`inj-D1`, `inj-D2`, `inj-D3`) gave the same PLC timeline after the Reset (t in seconds): STOP at +0.1, no reply at about +10 to +11, error 6 (target port not found) at +11.1 to +13.4, state 0 at +13.4, STOP at +13.8, **RUN at +13.9 to +14.0**. The PLC is away about 14 s. Because the real driver exits at +0.6 s, the builds below do not exit (patch 0004, an experiment that also makes the reconnect branch reachable).

| Build | Result after the PLC is back in RUN (about 2 min observed) |
|---|---|
| `noexit` (shipped driver, `exit(-1)` removed) | **Stuck**: `REFRESH_EXIT connectedAds=0 bulk0.readSize=16`, every group fails with 1797, 5805 failures, never recovers. The `connect()` calls made during the 14 s outage did not set `connectedAds_`, and nothing calls it again once the poll succeeds. |
| `f12noexit` (F10 + F12 + no exit) | The group failure is fixed: `REFRESH_CALL connectedAds=1`, refresh takes 12.8 s, `readSize` restored, 0 group failures. **But about 450 per-symbol reads per second still fail**, 7155 of about 8956 distinct symbols in 30 s, for as long as observed (more than 3 min). |
| `f12noexitdbg` (same plus patch 0005 logging the sub-read status) | Every failing sub-read returns **0x710 `ADSERR_DEVICE_SYMBOLNOTFOUND`** (154438 of 154438). |

So after a real PLC restart the symbolic handles obtained at boot are gone from the PLC (0x710), and the driver's refresh does not obtain new ones. F10 is necessary but not sufficient here (it was sufficient for stalls, which do not change the PLC's handles). This confirms H4 on this PLC. A restart of the IOC (the shipped `exit(-1)` path) re-resolves the handles, which is why the shipped driver only fails permanently when it survives the event. Evidence: `evidence/second-system/react-A1`, `react-B1`, `inj-C1`, `inj-D1-noexit`, `inj-D2-f12noexit`, `inj-D3-f12noexitdbg` (each with `summary.txt` where counts were taken).

#### Prototype fixes for F15 (builds that do not exit, Reset injected with `inject_trial.sh`)

Patch 0006 (F15) re-resolves all symbol handles in `refreshParams` before the parameters are rebuilt. Patch 0007 (F15b) gets a new handle per symbol for reads that return 0x710 and is retried every 5 s from the cyclic thread. Outcome is measured over 100 s after the Reset (`classify_inject.py`).

| Build | Trials | Outcome |
|---|---|---|
| `f15noexit` (F10 + F12 + F15) | 3 | 2 NOEFFECT (0 failed reads), 1 STUCK: 4 symbols kept returning 0x710 for the whole 100 s (196 failed reads) |
| `f15bnoexit` (F10 + F12 + F15 + F15b) | 5 | 5 NOEFFECT, 0 failed reads, refresh about 15 s each |
| `f15bonly` (F10 + F12 + F15b, no F15) | 3 | not usable, see below |

The first `f15bonly` build deadlocked in 1 of 2 trials (the new function took the asyn lock while the bulk thread holds `bulkReadInfoMutex_` and then takes the asyn lock; fixed in patch 0007). After the fix a batch was started but interrupted, and **one IOC was left running overnight against the PLC** (run `inject-20261118-171556-f15bonly2/r2`). It stayed stuck from 17:19 to 23:17 with about 450 failed 0x710 reads per second (9.7 million in total, 1.6 million per hour) and the F15b path did not stop the failures: its first call logged 7155 new handles at 21:59 and the failures only ended at 23:17:15. The PLC logged about 3500 `CAdsWatchServerR0::AdsGetDynSymbolArrayEntry, Handle(0x1801aad), m_nDynSymbolPools(2000)` errors, which are most likely these stale-handle reads. Conclusions so far:

- F15 alone is not enough (1 of 3 left 4 symbols stale), F15 + F15b worked in 5 of 5, and F15b alone is not reliable and needs another look (why a per-symbol retry took hours and did not stop the reads).
- A stuck driver is not harmless to the PLC. Every stale read is a PLC-side error, at about 450 per second here. Any fix needs a bounded retry with backoff, and any experiment must be time-limited (`inject_trial.sh` stops each trial; do not run a driver build that survives a restart without a watchdog).
- Open risk: F15 asks the PLC for 7352 new handles on every refresh and never releases the old ones. After a restart they are gone anyway, but a stall recovery runs the same refresh with valid old handles, so each stall could leak 7352 PLC handles. Check the PLC's dynamic handle limit and release old handles before re-resolving before this is used anywhere.

## Findings

| ID | Finding | Verdict | Evidence |
|---|---|---|---|
| F1 | `cyclicThread` calls `exit(-1)` after two consecutive failed 0.5 s polls (`notConnectedCounter_ > 1`). The reconnect branch (`counter & 2`) is unreachable. | Confirmed | A1, A2, A2b, A3, A4c: `EXIT_NEG1`, exit 255 |
| F2 | The poll blocks 5.5 s on a hard outage although `ADS_TIMEOUT_MS=1000`. Likely cause: `AdsSyncSetTimeoutEx` is applied only to the connect thread's ADS port. | Confirmed symptom, cause likely | Poll spacing 5.5 s in A2, A3, A2b, A4c. Check `AmsRouter.cpp` default timeout. |
| F3 | The IOC dies of SIGPIPE when the PLC closes the connection during boot. After `iocInit` the same write returns EPIPE (F9) and the process survives, so SIGPIPE appears to be ignored only once the CA server is up (to confirm in EPICS base). `Socket::write` uses `sendto(..., 0, ...)` with no `MSG_NOSIGNAL` and nothing ignores SIGPIPE. | Confirmed (exit 141, 1 run) | A4d cycle 1. Code: `ADS/AdsLib/Sockets.cpp` `Socket::write`. |
| F4 | The state poll starts only after `iocInit`, 44 to 60 s after start. The IOC has no view of PLC state in that window. | Confirmed | First `CYCLIC_STATE` at +44 s (A4), +54 s (A4c), +60 s (A4d) |
| F5 | During boot a PLC reactivation makes per-symbol lookups fail (0x710). The fallback path then retries symbol by symbol with no backoff. | Observed once | A4d cycle 1, 133 failures |
| F6 | The handle result is not always complete. One run resolved 7341 of 7363 (0x710 and 0x716) after an earlier activation. The next run resolved all. Failures are logged and not retried. | Observed once, cause unknown | run `rebuild-check` |
| F8 | With the link dead, one polling round takes 5 s per AMS port (5 ports here, about 25 s), and exit needs two rounds. Detection takes about 50 s, versus 0.6 s when the socket fails fast. | Confirmed | B1a: first failure 15:38:41, exit 15:39:27 |
| F9 | A cable pull or any event that closes the TCP socket kills the IOC within about 0.6 s. AdsLib reports "write frame failed: Broken pipe" and does not reconnect before the second poll. | Confirmed | B1b |
| F10 | The stuck loop. After a stall longer than the ADS timeout the cyclic thread sets `connectedAds_=0`. When the PLC answers again, `refreshParams` skips its body because `connectedAds_` is 0, yet still sets `bulkOK=1`. Nothing sets `connectedAds_` back to 1 (`connect()` is reached only from a branch that `exit(-1)` pre-empts). Bulk reads resume with `readSize=16` for 500 entries, so every read returns 0x705 for as long as the IOC runs. `refreshParams` is called again every 0.5 s and changes nothing. | Confirmed | C2: 600 failures in 43 s, log lines `REFRESH_EXIT ... connectedAds=0 bulk0.readSize=16` |
| F11 | A Windows Firewall block rule does not affect an already-established TCP connection. An 8 s block on port 48898 left the IOC untouched. Stall tests need a proxy or packet-level tool. | Confirmed | `plc-side/disturb-network.ps1` run, no IOC reaction |
| F12 (fixed, see patch) | AdsLib can lose its place in the TCP stream. If a frame read times out (`select() timeout`), `ReceiveFrame` calls `ReceiveJunk(bytesLeft)` without subtracting bytes the failed `Receive` already consumed. The skip over-reads into the next frame, later frames parse mid-way ("Frame to short to be AoE", "Port 0x206d is out of range"), and every request times out until the IOC exits via `exit(-1)` after 45 s. Cause confirmed by the fix: after the patch no desync warnings appear in 5 of 5 mid-frame stalls. | Confirmed (reproduced 4 of 5 with the split proxy, absent 5 of 5 after the fix) | run `20261117-172035-L8000-r4`; `AmsConnection.cpp` `ReceiveFrame`, `ReceiveJunk` |
| F13 | After every recovery the PLC sends a "Symbols changed" notification, so the IOC refreshes twice. Each refresh takes about 11 s for 7363 params with bulk reads paused. Data is stale for about 25 s after a stall that ends in recovery. | Observed (fix build) | run `20261117-170055-L6000-r1`: `REFRESH_EXIT` 17:02:39.0, then `adsSymbolsChangedCallback` and a second refresh to 17:02:51 |
| F14 | `exit(-1)` runs while the bulk thread is still issuing ADS calls on the dying socket. In one field run the IOC then died with SIGSEGV (core dumped), not exit code 255. | Observed once (user `ioc.log` 13:49:45) | "Sum read 2 failed: status -1" then "Segmentation fault (core dumped)". Not seen in my 9 exits (all code 255). A fix needs the threads stopped before exit. |
| F15 | After a real PLC restart (Write Control RESET to port 10000) the PLC is away about 14 s and its symbolic handles are invalid. A driver that survives and has F10 fixed still fails every symbol read with 0x710 until the IOC restarts. | Confirmed (`f12noexit`, `f12noexitdbg`) | `evidence/second-system/inj-D2-f12noexit`, `inj-D3-f12noexitdbg/summary.txt` |
| F16 | A driver that survives a PLC restart and is stuck on stale handles keeps issuing failing reads at about 450 per second and the PLC logs an error for each (`AdsGetDynSymbolArrayEntry`, about 3500 seen). Nothing throttles or backs off. | Confirmed (`inject-20261118-171556-f15bonly2/r2`, 6 h) | evidence summary.txt |
| F7 | `Sum read N failed` is an unthrottled `printf` with no timestamp, interleaved with other output. | Confirmed in code | `bulkReadThread`, line 1550 in 1.1.1 |

## Hypotheses still open

These come from reading the code. None was observed, because the IOC always exited before the code path was reached.

| ID | Hypothesis | Needed test |
|---|---|---|
| H1 | Confirmed as F10, with one correction: the trigger is `connectedAds_` stuck at 0, which makes `refreshParams` skip the rebuild. | done (C2) |
| H2 | `adsAddToBulkRead` does `readSize += plcSize + 4` on every call, including re-adds, so `readSize` inflates. | Same run as H1, log `BULK_ADD` after the refresh |
| H4 | Symbol handles are never re-resolved after reconnect (`symInfoResolved_` is never reset, `bSymbolicHandleValid` is not cleared on invalidate). | **Confirmed as F15** on the second system (0x710 on every symbol after a PLC restart, with F10 fixed). Open: why `updateParamInfoWithPLCInfo` in `refreshParams` leaves stale handles. |
| H7 | A PLC reactivation inside the boot window leaves stale handles, and the first poll sees `UNKNOWN to RUN` so nothing is invalidated. | Needs the PLC to return before the boot ends. F3 got in the way. |

Status 1797 is 0x705, `ADSERR_DEVICE_INVALIDSIZE` ("parameter size not correct"), from `ADS/AdsLib/standalone/AdsDef.h`. `ads-issue-observation.txt` calls it INVALIDDATA, which is wrong. The size error matches `readSize=16`, so the loop is not caused by stale handles. H2 and H4 may still be real but are not needed to explain it.

## Recommended fixes (Phase 3 mapping)

Ordered by value for the observed failures.

1. **Ignore SIGPIPE** (`signal(SIGPIPE, SIG_IGN)` at the start of `main`, before any ADS traffic) or pass `MSG_NOSIGNAL` in `Socket::write`. Removes the silent death (F3).
2. **Replace `exit(-1)` with a bounded reconnect ladder** (retry with backoff for N seconds, then exit). Make the reconnect branch reachable (F1).
3. **Apply the ADS timeout to every client port** (cyclic and bulk threads) so detection time matches `ADS_TIMEOUT_MS` (F2).
4. **Start the state poll before record init**, or re-check the PLC state at `iocInit` completion, and re-resolve handles if the state changed (F4, H7).
5. **Retry failed handle resolution** with backoff and a slow-recovery queue, and re-resolve all handles on reconnect (F5, F6, H4). This is the Phase 3 "Recover dynamic handles" item.
6. **Fix the reconnect state** (F10, verified: 7 of 8 stall trials recovered, none stuck). Restore `connectedAds_` when the state poll succeeds again (or call `connect()`), make `refreshParams` rebuild each bulk group's `readSize` and set `bulkOK=1` only after it did, and clear `port->refreshNeeded`. Flag failures per symbol. This is the Phase 3 "per symbol failure flags" item and the most direct fix for the stuck loop.
7. **Throttle and timestamp bulk failure logs** (F7).
8. **Fix the AdsLib frame skip** (F12, verified): `Receive` reports bytes consumed, and `ReceiveFrame` skips only `bytesLeft - consumed` after a timeout. The patch is 88 lines and touches AdsLib, so it needs an upstream check.
9. **Skip the second refresh** (F13): ignore a "Symbols changed" notification that arrives while a refresh is in progress or just after one, and rebuild the bulk groups faster than per-param ADS calls.

## Why the cable pulls did not show it

A cable pull closes the TCP session, so the second poll fails on a dead socket and the IOC exits (F9). The stuck loop needs the session to survive and the next poll to succeed, which is what a network stall does. The earlier incident log (`ads-issue-observation.txt`) shows the same sequence as C2: 1861, "InvokeId mismatch" warnings, RUN to INVALID to RUN, then a stream of 1797.

## Next runs

- Longer soak with the F10 + F12 build: 6 s to 12 s stalls in pause mode, 10 repeats each, plus a cut-mode run.
- Measure the second refresh (F13) and its 25 s data gap, and test a fix.
- Cut mode of the proxy (`--mode cut`) against the fix build, to confirm it behaves like the cable pull.
- Repeat A4d five times to count how often SIGPIPE versus `exit(-1)` occurs during boot.
- Rerun with the Phase 3 fixes applied one at a time, using the same harness.

## Artifacts

All under `profiling/`: `runs/<timestamp>-<scenario>/` hold `ioc.raw.log`, `sim.log`, `pvs.csv`, `meta.txt`. Instrumented module `ads-1.1.1-prof`, original driver `adsAsynPortDriver.1.1.1.orig.cpp`.
