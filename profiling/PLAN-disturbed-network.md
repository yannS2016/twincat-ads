# Plan: reproduce and fix ADS IOC failures on a disturbed network

Written 2026-11-17 for whoever continues this on the second system. Read `ads-network-resilience-profile.md` first (results so far); this file says what to do next and how to carry the harness over.

## 1. Goal

On the lab bench the IOC loses its PLC connection in three ways. We want to know which of them also happens on the disturbed network, how often, and whether the test patches remove them there.

| ID | Failure | Bench status | Test patch |
|---|---|---|---|
| F1 | `exit(-1)` after two failed 0.5 s state polls (0.6 s to 50 s after the first failure) | confirmed | none yet |
| F3 | SIGPIPE kills the IOC if the PLC closes the connection during boot (exit code 141) | confirmed, 1 run | none yet |
| F10 | After a stall of 6 s or more the IOC stays alive but every bulk read fails with 0x705 until restart | confirmed, 5 of 6 trials | `0002-F10` (verified) |
| F12 | AdsLib loses its place in the TCP stream after a mid-frame read timeout, every request then times out, IOC exits after about 50 s | confirmed, 4 of 5 trials | `0003-F12` (verified) |
| F13 | Two full refreshes after every recovery, about 25 s of stale data | observed | none yet |
| H5 | "2-3 restarts" after a PLC reactivation | not reproduced | none |

Success on the new network means: the shipped driver shows the field symptoms, and the patched driver does not, over enough trials to say so (section 6).

## 2. What we do not know (collect first)

Answer these before any fault injection. They decide which faults matter.

1. What is the disturbed network (WAN, VPN, Wi-Fi, a congested switch, a firewall or NAT in the path)? Where are the IOC host and the PLC relative to it?
2. How often does the field symptom occur, and which one: IOC exits, stuck `Sum read N failed`, or both? Any logs from the field IOC and its procServ (holdoff, restart limit)?
3. Is the PLC a Windows PLC or TcBSD, and how long does a config reactivation take there (the bench PLC is back in under 10 s)?
4. Do we have root on the IOC host for `tcpdump` and `tc`? Can we capture on the PLC side?
5. Are IOC host and PLC clock-synchronized (chrony or NTP)? Without that, PLC-side event times cannot be lined up with IOC logs.

## 3. Prerequisites on the new system

| Item | Why | How |
|---|---|---|
| EPICS base 7.0.3.1 and asyn 4.42 | build the module | match the bench (`RELEASE_SITE`) |
| Site module twincat-ads R2.0.0-1.1.1 | patches target this source (the git tree here is R2.0.0-1.0.0 and differs) | set `SITE_MODULE` |
| A built ads-ioc R2.2.1 tree | the IOC the module links into | set `ADS_IOC_SRC` |
| The sample IOC repo with a regenerated `st.cmd` | boots with 7363 symbols | pytmc 2.22.1 in a venv (2.21.1 fails), `make IOC_TOP=<ads-ioc> `, prefix `PLC:TST` |
| Python 3.9+ with pyepics | PV watcher (optional) | any env, or use `camonitor` |
| `tcpdump`, and `tc`/`nft` if root is available | packet evidence and fault injection | check with `which` |
| One IOC per PLC AMS Net ID | two IOCs with the same Net ID drop each other | stop other IOCs first |

Pitfalls found on the bench: the old prefix `PLC:motion_abstraction_sample:` makes long-named records fail to create (the IOC then loads only 48 symbols); a Windows Firewall block rule does not touch an established TCP connection; `pkill -f <name>` from a shell whose command line contains the name kills that shell.

## 4. Phase 0: carry the harness over (half a day)

1. Clone the branch, change into `profiling/`, read `README.md`.
2. Build the three variants (about 30 s each after the first):
   `./build-profiling-ioc.sh base build`, then `f10`, then `f12`. Check each binary with `strings`: base has `PROF %s`, f10 adds `FIX_CONNECTEDADS`, f12 adds `bytes already consumed`.
3. Edit addresses: the harness defaults to IOC host 192.168.0.6 and PLC 192.168.0.9. Set `HOSTIP`, `PLCIP`, and the sample repo's `st.cmd` (`IPADDR`, `AMSID`). `run_scenario.sh` stages the sample IOC files from `REPO` and can redirect the IOC to a proxy with `IOC_IPADDR`.
4. Smoke test: `./run_scenario.sh smoke`, expect `HANDLES_DONE resolved=N total=N` and a `CYCLIC_STATE port=851 ... state=21->5` line about 60 s after start, no `BULK_FAIL`. Then `python3 analyze.py runs/<dir>/ioc.raw.log`.
5. Check the proxy path: `./ladder.sh "1000" 1 smoke` should give `NOEFFECT`.

## 5. Phase 1: baseline on the real network, no injection (1 to 2 days)

Goal: see the field failures happen without us causing them.

1. Run the shipped driver untouched (the stock `ads-ioc` binary, `./run_scenario.sh <name> --shipped`) for 24 h under `procserv_sim.sh <name> <holdoff>` with the real holdoff. This confirms the symptoms exist here and gives a rate.
2. Repeat with the `base` build (log lines only) for 24 h. Compare the two logs: the instrumentation must not change behavior.
3. In parallel capture `tcpdump -i <if> -w adsring -C 100 -W 20 tcp port 48898` (needs root) and log RTT with `ping -D -i 0.5 <plc> > ping.log`.
4. Classify every IOC exit or stuck period with `analyze.py` and `classify.py` (exit code 255 = `exit(-1)`, 141 = SIGPIPE, stuck = `BULK_FAIL` with status 1797 and `readSize=16`, desync = "Frame to short" or "out of range" lines).
5. Write down what the network does: RTT distribution, loss, how long stalls last when the symptom appears (match packet capture gaps against the IOC `BULK_FAIL` times).

Exit criteria: at least one field-type event captured with matching IOC log and packet capture, or a clear statement that the shipped driver is stable on this network (then the disturbance is a PLC or procServ issue, go to H5).

## 6. Phase 2: active disturbance matrix (2 to 3 days)

Each trial: fresh IOC, fresh fault, 60 s observation, classify. Use `ladder.sh` for proxy faults. Repeat each cell at least 10 times, the stall length where outcomes vary (6 s on the bench) at least 20 times.

| Fault | Tool | Parameters | What it tests |
|---|---|---|---|
| Stall, TCP stays open | `ads_stall_proxy.py --mode pause` | 1, 2, 3, 4, 5, 6, 8, 12 s | F10, F13, bench threshold at the 5 s ADS timeout |
| Stall starting mid-frame | `--mode split` | 8 s | F12 |
| Socket cut | `--mode cut` | n/a | F1, F9 (exit within 0.6 s) |
| Packet loss | `tc qdisc add dev <if> root netem loss 1% 5% 10% 30%` | per value, 5 min each | does TCP hide it, at what loss does ADS time out |
| Delay and jitter | `netem delay 100ms 50ms`, 500 ms, 1 s | per value, 5 min each | slow polls, spurious timeouts |
| Reordering, duplication | `netem reorder 25% duplicate 5%` | 5 min | AdsLib framing |
| Blackhole window | `nft` drop of port 48898 both ways for N s, no reset | 1 to 12 s | same as pause but with real TCP retransmit timing |
| Reset | `iptables -A INPUT -p tcp --sport 48898 -j REJECT --reject-with tcp-reset` | brief | F9 |
| PLC reactivation | XAE activate while the IOC polls, and while it boots (`cycler.sh`) | 10 each | F1, F3, H5 |
| Boot-time disturbance | start the IOC during any fault above | 10 each | F3, F5 (symbol lookup failures at boot) |

Always remove `tc`/`nft` rules after a run (`tc qdisc del dev <if> root`, `nft flush ruleset` only if it was empty before). Do the disturbance on the IOC host side so the PLC stays reachable for XAE.

For each fault run three builds: shipped or `base`, `f10`, `f12`. Record outcome per trial: NOEFFECT, RECOVERED, STUCK, EXIT (code), SIGPIPE, desync warning count, time to recover.

## 7. Phase 3: fixes for the remaining failures (parallel, 3 to 5 days)

Order by expected value on a disturbed network:

1. **F3** ignore SIGPIPE at startup (`signal(SIGPIPE, SIG_IGN)` early in `main`, or `MSG_NOSIGNAL` in `Socket::write`). Test with `cycler.sh` and a boot-time reactivation, expect exit code 141 never appears.
2. **F1** replace `exit(-1)` with a bounded reconnect ladder, and make the `notConnectedCounter_ & 2` reconnect branch reachable. Test: `pause` stalls up to 30 s, expect no exit inside the retry window and a clean recovery.
3. **F2** apply `ADS_TIMEOUT_MS` to the cyclic and bulk threads' ADS ports (state poll currently blocks 5.5 s per port, about 50 s to detect a dead link with 5 AMS ports). Check `AmsRouter.cpp` for the default.
4. **F13** ignore the "Symbols changed" notification during and right after a refresh, and rebuild bulk groups faster. Metric: stale-data time after a 8 s stall, now about 25 s.
5. **Phase 3 of `twincat-ads_update_plan.md`**: per-symbol failure flags, handle re-resolution and retry with backoff, slow recovery queue. Use the matrix above as the regression suite.

Each fix is a patch under `patches/`, built through `build-profiling-ioc.sh` (extend its variant list), and checked against the matrix before it moves into the driver source.

## 8. Acceptance criteria

- Baseline reproduces a field symptom on the disturbed network (Phase 1), or Phase 1 states it does not.
- With all patches: 0 STUCK, 0 desync, 0 SIGPIPE, 0 unplanned `exit(-1)` for stalls up to 12 s in at least 20 trials per stall length, and recovery (PVs valid again) within a stated bound (propose 30 s, to be agreed).
- For stalls longer than the retry window, the IOC exits cleanly with a logged reason and procServ restarts it.
- The report is updated with a results table for each build and fault, and the patches are ported to the real source tree (see section 9).

## 9. Porting the patches to the source tree

The git tree here (`R2.0.0-1.0.0`) has a driver of 7876 lines. The patches target the site R2.0.0-1.1.1 driver (7481 lines) and apply to it with `patch -p1`. Before upstreaming:

1. Diff the two driver files around `cyclicThread`, `refreshParams`, `invalidateParams` (the same code exists in both but at different line numbers).
2. Re-apply `0002-F10` by hand on `R2.0.0-1.0.0` and run `tests/unit` (GoogleTest, includes `Stage9_ReconnectSucceeds`).
3. F12 edits AdsLib (`ADS/AdsLib/standalone/AmsConnection.cpp` and `AmsConnection.h`). Check upstream Beckhoff ADS for the same `ReceiveFrame` code before carrying a fork patch.
4. Extend `tests/server/ads_test_server.py` with a "stall" or "drop session" switch so F10 and F12 get a unit-level regression test.

## 10. Risks and safety

- Do not run fault injection on a production PLC or IOC. Use a test PLC, or agree a window.
- Disturbing a shared network affects other users. Scope `tc`/`nft` rules to the IOC host and the PLC address only.
- A forgotten `tc` rule or firewall rule persists. Every script cleans up on exit, but check with `tc qdisc show` and `nft list ruleset` after each session.
- The instrumented IOC logs a lot (about 7,200 `BULK_ADD` lines per start). Watch disk space and rotate `runs/`.
- Two IOCs with the same AMS Net ID evict each other. Check `pgrep adsIoc` before starting.

## 11. Estimated effort

Phase 0 half a day, Phase 1 one to two days (mostly waiting), Phase 2 two to three days (about 10 trials per cell, 2.5 minutes each), Phase 3 three to five days, writing and porting two days. About two weeks, less if the baseline shows only one of the failures.
