# ADS IOC resilience profiling

Harness, patches, evidence and plan for the PLC reactivation and network-disturbance failures of the ads-ioc driver.

Start here:
- `ads-network-resilience-profile.md`: results and findings so far (bench network, Windows 10 PLC).
- `PLAN-disturbed-network.md`: what to do next on the disturbed network, and how to carry this over.
- `ads-issue-observation.txt`: the original field incident log.

## What the IOC runs

The sample IOC (`lcls-plc-motion-abstraction-sample`) links the **site** module `twincat-ads R2.0.0-1.1.1`, not the sources in this tree (`R2.0.0-1.0.0`, 7876-line driver). The patches here target the 1.1.1 sources (7481 lines) and apply with `patch -p1` from the module root:

| Patch | Content |
|---|---|
| `patches/0001-instrumentation-profLog.patch` | log-only `PROF` lines (bulk failures, state polls, invalidate/refresh, handle resolution, exits). No behavior change. |
| `patches/0002-F10-restore-connectedAds.patch` | F10: restore `connectedAds_=1` when the state poll succeeds again. Fixes the stuck `Sum read N failed` loop. |
| `patches/0003-F12-receiveframe-skip.patch` | F12: AdsLib `ReceiveFrame` skips only the unread remainder after a timeout. Fixes the stream desync. |
| `patches/0004-experiment-noexit.patch` | Experiment, not a fix: `cyclicThread` logs `EXIT_SKIPPED` instead of calling `exit(-1)`, so the IOC survives a PLC restart and the reconnect branch becomes reachable. |
| `patches/0005-log-sub-read-status.patch` | Log only: adds the per-symbol status (for example `0x710`) to the `bulk read for ... failed` line. |
| `patches/0006-F15-reresolve-handles.patch` | Prototype F15: `refreshParams` re-resolves all symbol handles (SUMUP) before rebuilding. Does not release the old handles yet. |
| `patches/0007-F15b-rehandle-failed-symbols.patch` | Prototype F15b: new handle per symbol whose bulk read returned 0x710, retried every 5 s from `cyclicThread`. |

`adsAsynPortDriver.1.1.1.orig.cpp` is the unmodified 1.1.1 driver, for reference.

## Build

    SITE_MODULE=<twincat-ads R2.0.0-1.1.1 dir> ADS_IOC_SRC=<built ads-ioc R2.2.1 tree> \
        ./build-profiling-ioc.sh base|f10|f12 [out_dir]

Variants: `base` (instrumentation), `f10` (+F10), `f12` (+F10 +F12), `noexit` (base +0004), `f12noexit` (f12 +0004), `f12noexitdbg` (f12noexit +0005), `f15noexit` (+0006), `f15bnoexit` (+0007), `f15bonly` (f12noexitdbg +0007). The script copies the module, applies the patches, builds, copies the ads-ioc tree, points it at the new module and relinks. Result: `<out>/ads-ioc-<variant>/bin/<arch>/adsIoc`. Use that directory as `ADS_IOC_DIR` below.

The sample IOC files need a regenerated `st.cmd` and database with pytmc 2.22.1 (2.21.1 fails on the array pragma `1..3 | 1..3`) and prefix `PLC:TST`. A long prefix makes records fail to create. See the report, "Test bed".

## Scripts

| Script | Purpose |
|---|---|
| `run_scenario.sh <name> [--shipped]` | start the sample IOC with raw, stamped logging into `runs/<ts>-<name>/`. Env: `ADS_IOC_DIR`, `REPO` (sample IOC repo), `IOC_IPADDR` (point the IOC at a proxy), `RUN_DIR`. Writes `IOC_RC=<exit status>`. |
| `procserv_sim.sh <name> [holdoff_s]` | emulates procServ: restart after exit. |
| `cycler.sh <name> [cycle_s]` | restarts the IOC every N s so a PLC event lands in a boot window. |
| `ads_stall_proxy.py` | TCP proxy that stalls the ADS connection (`--mode pause`, `cut`, `split`). Stays on the IOC host, keeps the IOC host's source address. Logs `EOF`/`ERR`/`GAP` (PLC silence over 1 s) events. With `--inject-reset`, `kill -USR2 <pid>` (or `--inject-delay <s>`) sends the ADS Write Control RESET that XAE's "Restart TwinCAT System in Run Mode" uses (port 10000, AdsState 2, DevState 0) on the IOC's own connection, and drops the PLC's reply. Restarts the PLC system, so use it only on a test PLC. |
| `ladder.sh "<ms,ms>" <repeats> <tag>` | one fresh IOC and proxy per trial, one stall, classify. Env: `MODE`, `IOCDIR`, `HOSTIP`, `PLCIP`, `FIRST`, `OBS`. |
| `run_stall_test.sh` | one IOC behind the proxy with a stall schedule. |
| `classify.py <run_dir>` | outcome of one trial: NOEFFECT, RECOVERED, STUCK, EXIT, SIGPIPE, plus desync count. |
| `analyze.py <log>` | event timeline and summary of an IOC log. |
| `watch_pvs.py <csv>` | PV watcher (pyepics). Set `EPICS_CA_ADDR_LIST` to the IOC host. |
| `inject_trial.sh <tag> [repeats]` | Reset-injection trials: fresh proxy and IOC, one Write Control RESET to the PLC's system service (the PLC is away about 14 s), 100 s observation, classify, stop. Env: `IOCDIR`, `HOSTIP`, `PLCIP`, `REPO`, `STEADY`, `OBS`, `BOOT`, `MAXLIFE` (hard cap on the IOC and proxy processes), and `IOC_NAME`/`DB_BASE` for another project (see `run_scenario.sh`). Restarts the PLC each trial. Never leave an IOC build that survives the restart running unattended. |
| `classify_inject.py <run_dir> [obs_s]` | outcome of one injection trial: NODETECT, NOEFFECT, RECOVERED, STUCK, EXIT, SIGPIPE, with group and per-symbol failure counts. |
| `handle_usage.py --plc <ip> --local-net-id <id>` | read-only: PLC symbol count and dynamic symbol handle limit and use (ADS Read, index group 0xF00F). Stop the IOC first if the Net ID is the IOC's. |
| `trim-evidence.sh <dest> <dir>...` | copy the committable part of run and ladder dirs (filtered log, proxy log, summary) into an evidence directory. |
| `plc-side/disturb-network.ps1` | Windows PLC script. A firewall block does **not** stall an established TCP connection (tested), so it is kept for reference only. |

Addresses default to IOC host 192.168.0.6 and PLC 192.168.0.9 (`HOSTIP`, `PLCIP`). `st.cmd` in the sample repo holds the IOC's own `IPADDR` and `AMSID`.

Stop processes by pid. `pkill -f <name>` kills the calling shell if its command line contains the name.

## Evidence

`evidence/<run>/` holds the trimmed logs of every run in the report (`ioc.filtered.log.gz` keeps `PROF`, AdsLib warnings and errors, `SIM_*`, `IOC_RC`, proxy lines; `BULK_ADD` lines and symbol-lookup noise are dropped), plus `proxy.log`, `sim.log`, `meta.txt`, `pvs.csv`. `evidence/ladder-*/summary.csv` has the per-trial outcomes. Raw logs (about 1 GB) are not committed. `evidence/second-system/` holds the runs from the second host (PLC 172.21.148.154) in the same layout; create it with `trim-evidence.sh <dest> <run_dir|ladder_dir>...`.

Run names: A1, A2, A2b, A3, A4c, A4d are PLC reactivation runs; B1 is the cable pull; C1, C2 are the first proxy runs; `ladder-*-base`, `-fix`, `-split-*` are the stall ladders.
