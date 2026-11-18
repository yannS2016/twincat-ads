#!/bin/bash
# IOC (procServ-sim) behind the stall proxy.  Usage: run_stall_test.sh <scenario> <stalls_ms,csv> <repeats> <first_delay_s> <gap_s> [pause|cut]
HERE=$(cd "$(dirname "$0")" && pwd)
SCEN=${1:?scenario}; STALLS=${2:?stalls}; REP=${3:-1}; FIRST=${4:-90}; GAP=${5:-180}; MODE=${6:-pause}
HOSTIP=${HOSTIP:-192.168.0.6}; PLCIP=${PLCIP:-192.168.0.9}
export RUN_DIR=$HERE/runs/$(date +%Y%m%d-%H%M%S)-$SCEN
mkdir -p "$RUN_DIR"; echo "run dir: $RUN_DIR"
setsid python3 -I "$HERE/ads_stall_proxy.py" --listen $HOSTIP:48898 --target $PLCIP:48898 --src $HOSTIP --mode $MODE \
  --stalls "$STALLS" --repeats $REP --first-delay $FIRST --gap $GAP --log "$RUN_DIR/proxy.log" > /dev/null 2>&1 < /dev/null &
echo $! > "$RUN_DIR/proxy.pid"
sleep 1
IOC_IPADDR=$HOSTIP setsid "$HERE/procserv_sim.sh" "$SCEN" 15 > /dev/null 2>&1 < /dev/null &
echo $! > "$RUN_DIR/sim.pid"
