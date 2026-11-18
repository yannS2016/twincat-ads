# Guide: apply all changes on another system

Follow this top to bottom on the new system. Every command was run on the bench host (RHEL 9, EPICS 7.0.3.1). Then continue with `PLAN-disturbed-network.md`.

## 0. What "all changes" are

| Where | What | Ref |
|---|---|---|
| `lcls-plc-motion-abstraction-sample`, branch `enh_mcs2_integrated_nc_regression` | `PREFIX := PLC:TST` in the IOC Makefile; `st.cmd` and `envPaths` regenerated with pytmc 2.22.1; `bsd-setup.md` added; `scripts/test/*` removed (47 files) | commit `9782ad7` |
| `twincat-ads`, branch `enh_network_resilience_profiling` | `profiling/`: patches, harness scripts, evidence, report, plan, this guide | commits `29bc3b9` and the guide commit on top |

The driver patches are not applied to any source tree. They are applied to a copy of the site module by `build-profiling-ioc.sh` (step 4), so nothing in the real driver changes until you port them (plan section 9).

Neither branch was pushed when this was written. Push them, or move them with `git bundle create x.bundle <branch>` and `git clone x.bundle`.

## 1. Get the code

    git clone https://github.com/yannS2016/twincat-ads.git
    git -C twincat-ads checkout enh_network_resilience_profiling
    git clone https://github.com/yannS2016/lcls-plc-motion-abstraction-sample.git
    git -C lcls-plc-motion-abstraction-sample checkout enh_mcs2_integrated_nc_regression

The twincat-ads checkout is `R2.0.0-1.0.0`'s repo, so the files are in `profiling/` at its root. Below, `$TA` is that directory and `$SAMPLE` is the sample repo.

## 2. Prerequisites

| Need | Check |
|---|---|
| EPICS base R7.0.3.1 and asyn R4.42 | paths in `RELEASE_SITE` (`EPICS_SITE_TOP`, `BASE_MODULE_VERSION`) |
| Site module `twincat-ads R2.0.0-1.1.1`, built | `ls $SITE_MODULE/lib/$EPICS_HOST_ARCH/libads.a` |
| ads-ioc R2.2.1, built (`bin/<arch>/adsIoc` exists) | the IOC the sample boots with |
| Python 3.9+, `uv` (or pip), `patch`, `make`, `g++` | the build script uses them |
| pyepics (watcher only) | or use `camonitor` |
| Root, for `tcpdump` and `tc` | only needed for plan Phase 1/2 packet work |

Export these once (adjust paths):

    export SITE_MODULE=/cds/group/pcds/epics/R7.0.3.1-2.0/modules/twincat-ads/R2.0.0-1.1.1
    export ADS_IOC_SRC=$HOME/ads-ioc/R2.2.1
    export EPICS_HOST_ARCH=rhel9-x86_64

## 3. Regenerate the sample IOC files (pytmc 2.22.1)

pytmc 2.21.1 fails on the array pragma `1..3 | 1..3`, so use 2.22.1 in its own venv:

    uv venv --python 3.11 $HOME/pytmc222
    uv pip install --python $HOME/pytmc222/bin/python pytmc==2.22.1 setuptools
    cd $SAMPLE/iocBoot/ioc-motion-abstraction-sample
    PATH=$HOME/pytmc222/bin:$PATH make IOC_TOP=$ADS_IOC_SRC

Expected: `Installing: st.cmd` and `Installing: motion_abstraction_sample.db`. `git status` then shows only `st.cmd` and `envPaths` changed, because the database and archive come out identical.

`st.cmd` and `envPaths` hold machine-specific values (hashbang, `ENGINEER`, `TOP`, `IOC_TOP`). Regenerate them on each system, as above, instead of copying them.

**Point the IOC at your PLC.** The address comes from the TwinCAT project (`192.168.0.9`, AMS `192.168.0.9.1.1` on the bench). On another network set it in the Makefile and rebuild:

    PLC_HOSTNAME := <plc ip or name>
    PLC_AMS_NET_ID := <plc ams net id>

The prefix must stay `PLC:TST`. A longer prefix makes records with long names fail to create ("Can't create record"), and the IOC then loads only 48 symbols.

## 4. ADS route and network

The PLC needs a route for the IOC host's AMS Net ID (`<ioc host ip>.1.1`) and its firewall must pass TCP 48898 from the IOC host. Windows PLC: add the route in the TwinCAT router. TcBSD: see `$SAMPLE/bsd-setup.md`. Only one IOC may run per AMS Net ID, because a second connection from the same Net ID evicts the first.

## 5. Build the profiling IOCs

    cd $TA/profiling
    for v in base f10 f12; do ./build-profiling-ioc.sh $v build; done

Check each binary:

    strings build/ads-ioc-base/bin/$EPICS_HOST_ARCH/adsIoc | grep -c "PROF %s"                # 1
    strings build/ads-ioc-f10/bin/$EPICS_HOST_ARCH/adsIoc  | grep -c FIX_CONNECTEDADS         # 1
    strings build/ads-ioc-f12/bin/$EPICS_HOST_ARCH/adsIoc  | grep -c "bytes already consumed" # 1

Variants: `base` is log lines only, `f10` adds the connectedAds fix, `f12` adds the AdsLib frame-skip fix. The patches target `R2.0.0-1.1.1`. If your site module is another version, `patch` may reject hunks; port them by hand.

## 6. Smoke test the IOC

    export REPO=$SAMPLE ADS_IOC_DIR=$TA/profiling/build/ads-ioc-base
    ./run_scenario.sh smoke          # stop with Ctrl-C after about 90 s
    python3 -I analyze.py runs/*-smoke/ioc.raw.log

Expected (bench numbers; the symbol count depends on the PLC build):
- `HANDLES_DONE resolved=7363 total=7363`
- no `Can't create record` lines
- the first `CYCLIC_STATE port=851 ... state=21->5` about 60 s after start
- no `BULK_FAIL` lines

If handles resolve to 48, the prefix or database did not load; recheck step 3.

## 7. Smoke test the stall proxy

The IOC host address must be the one the PLC routes to. Run:

    export HOSTIP=<ioc host ip> PLCIP=<plc ip> IOCDIR=$TA/profiling/build/ads-ioc-f12
    ./ladder.sh "1000" 1 smoke

Expected outcome `NOEFFECT` (a 1 s stall is absorbed). Then `./ladder.sh "8000" 1 pause8` should give `RECOVERED` on `f12`, and `STUCK` on `base` (5 of 6 trials on the bench).

## 8. Continue

Follow `PLAN-disturbed-network.md`: Phase 1 baseline on the real network, Phase 2 fault matrix, Phase 3 fixes. Results table and findings go into `ads-network-resilience-profile.md`.

## 9. Pitfalls seen on the bench

- Do not run `pkill -f <name>` from a shell whose command line contains `<name>`; it kills that shell. Stop by pid.
- A Windows Firewall block rule does not stall an established TCP connection. Use the proxy or `tc`/`nft`.
- The IOC needs about 60 s after start before its state poll runs. Do not time faults against the start of the log.
- `run_scenario.sh` stages the sample IOC files into `runs/<dir>/stage`, so the sample repo stays clean. Running the IOC from `$SAMPLE/iocBoot/...` instead writes autosave files (`info_*.sav`, `info_*.req`, `*.archive`) into the repo. Do not commit them.
- The instrumented IOC writes about 7,200 `BULK_ADD` lines per start. Rotate or delete old `runs/` directories.
