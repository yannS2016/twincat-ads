#!/bin/bash
# Reset-injection trials: fresh proxy + IOC, wait for steady state, send one Write Control RESET to the PLC's system
# service (restarts the TwinCAT system, the PLC is away about 14 s), observe, classify, stop.
# Usage: inject_trial.sh <tag> [repeats=1]     Results: runs/inject-<ts>-<tag>/summary.csv
# Env: IOCDIR (ioc dir of the build under test), REPO, HOSTIP, PLCIP, STEADY (s after first poll, default 10), OBS (s after the Reset, default 100),
#      READY (grep pattern for the first state poll; use 'Device "Plc30 App" connected' for builds without PROF lines),
#      BOOT (max s to wait for the first state poll, default 400), MAXLIFE (hard cap in s on the IOC and proxy processes, default 900)
# IOC_NAME and DB_BASE (see run_scenario.sh) select another project.
# The PLC restarts on every trial: only use it on a test PLC.
HERE=$(cd "$(dirname "$0")" && pwd)
TAG=${1:?tag}; REP=${2:-1}
IOCDIR=${IOCDIR:?IOCDIR}; HOSTIP=${HOSTIP:?HOSTIP}; PLCIP=${PLCIP:?PLCIP}
READY=${READY:-CYCLIC_STATE port=851 .*state=21->5}; STEADY=${STEADY:-10}; OBS=${OBS:-100}; BOOT=${BOOT:-400}; MAXLIFE=${MAXLIFE:-900}
L=$HERE/runs/inject-$(date +%Y%m%d-%H%M%S)-$TAG; mkdir -p "$L"
echo "outcome,reset_to_run_s,group_fail,sub_fail,ioc_rc,last_fail_after_reset_s,rehandle_calls,run_dir" > "$L/summary.csv"
echo "ioc_dir=$IOCDIR steady=$STEADY observe=$OBS" > "$L/meta.txt"
stop() { kill -TERM -- -$1 2>/dev/null; }
for r in $(seq 1 $REP); do
  RD=$L/r$r; mkdir -p "$RD"
  setsid timeout $((MAXLIFE + 30)) python3 -I "$HERE/ads_stall_proxy.py" --listen $HOSTIP:48898 --target $PLCIP:48898 --src $HOSTIP \
    --inject-reset --probe-s $OBS --log "$RD/proxy.log" > /dev/null 2>&1 < /dev/null & PP=$!
  sleep 1
  RUN_DIR=$RD IOC_IPADDR=$HOSTIP ADS_IOC_DIR=$IOCDIR setsid timeout $MAXLIFE "$HERE/run_scenario.sh" inject-$TAG > /dev/null 2>&1 < /dev/null & IP=$!
  echo "$(date +%H:%M:%S) trial $r/$REP run=$RD" | tee -a "$L/progress.log"
  timeout $BOOT bash -c "until grep -q '$READY' $RD/ioc.raw.log 2>/dev/null; do sleep 2; done"
  sleep $STEADY
  # $PP is the timeout wrapper; the signal must go to its python child, which does the injection
  kill -USR2 $(pgrep -P $PP -f ads_stall_proxy)
  sleep $OBS
  stop $IP; stop $PP; sleep 4
  echo "$(python3 -I "$HERE/classify_inject.py" "$RD" $OBS),$RD" | tee -a "$L/summary.csv" >> "$L/progress.log"
  sleep 10
done
echo "$(date +%H:%M:%S) INJECT_DONE" >> "$L/progress.log"
