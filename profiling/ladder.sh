#!/bin/bash
# Stall ladder: for each stall length x repeat, start a fresh proxy + fresh IOC, stall once, observe, classify, stop.
# Usage: ladder.sh "<ms,ms,...>" <repeats> [tag]   Results: runs/ladder-<ts>-<tag>/summary.csv
HERE=$(cd "$(dirname "$0")" && pwd)
LIST=${1:?stalls}; REP=${2:-3}; TAG=${3:-base}; IOCDIR=${IOCDIR:-$HERE/ads-ioc-prof}
FIRST=${FIRST:-85}; OBS=${OBS:-60}; HOSTIP=${HOSTIP:-192.168.0.6}; PLCIP=${PLCIP:-192.168.0.9}
L=$HERE/runs/ladder-$(date +%Y%m%d-%H%M%S)-$TAG; mkdir -p "$L"
echo "outcome,stall_ms,first_fail_s,n_bulk_fail,n_1797,ioc_rc,exit_after_s,run_dir" > "$L/summary.csv"
echo "ioc_dir=$IOCDIR first=$FIRST observe=$OBS" > "$L/meta.txt"
stop() { kill -TERM -- -$1 2>/dev/null; }
for r in $(seq 1 $REP); do
  for ms in ${LIST//,/ }; do
    RD=$HERE/runs/$(date +%Y%m%d-%H%M%S)-L$ms-r$r; mkdir -p "$RD"
    setsid python3 -I "$HERE/ads_stall_proxy.py" --listen $HOSTIP:48898 --target $PLCIP:48898 --src $HOSTIP \
      --mode ${MODE:-pause} --stalls $ms --repeats 1 --first-delay $FIRST --log "$RD/proxy.log" > /dev/null 2>&1 < /dev/null & PP=$!
    sleep 1
    RUN_DIR=$RD IOC_IPADDR=$HOSTIP ADS_IOC_DIR=$IOCDIR setsid "$HERE/run_scenario.sh" L$ms > /dev/null 2>&1 < /dev/null & IP=$!
    echo "$(date +%H:%M:%S) trial stall=${ms}ms rep=$r run=$RD" | tee -a "$L/progress.log"
    timeout $((FIRST + ms/1000 + OBS + 60)) bash -c "until grep -q 'STALL_END' $RD/proxy.log 2>/dev/null; do sleep 2; done"
    sleep $OBS
    stop $IP; stop $PP; sleep 4
    echo "$(python3 -I "$HERE/classify.py" "$RD" $OBS),$RD" | tee -a "$L/summary.csv" >> "$L/progress.log"
    sleep 10
  done
done
echo "$(date +%H:%M:%S) LADDER_DONE" >> "$L/progress.log"
