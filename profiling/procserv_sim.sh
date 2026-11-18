#!/bin/bash
# Emulates procServ auto-restart: runs the IOC, and on exit waits HOLDOFF seconds then restarts it.
# Usage: procserv_sim.sh <scenario> [holdoff_s=15] [--shipped]    Stop with Ctrl-C (or kill the script pid).
HERE=$(cd "$(dirname "$0")" && pwd)
SCEN=${1:?scenario}; HOLD=${2:-15}; EXTRA=$3
export RUN_DIR=${RUN_DIR:-$HERE/runs/$(date +%Y%m%d-%H%M%S)-$SCEN}
mkdir -p "$RUN_DIR"; echo "run dir: $RUN_DIR" >&2
echo "holdoff=$HOLD" > "$RUN_DIR/meta.txt"
n=0
trap 'echo "$(date +%H:%M:%S.%3N) SIM_STOP" >> "$RUN_DIR/sim.log"; exit 0' INT TERM
while true; do
  n=$((n+1)); echo "$(date +%H:%M:%S.%3N) SIM_START n=$n" | tee -a "$RUN_DIR/sim.log" >> "$RUN_DIR/ioc.raw.log"
  "$HERE/run_scenario.sh" "$SCEN" $EXTRA > /dev/null 2>&1 < /dev/null
  rc=$?
  echo "$(date +%H:%M:%S.%3N) SIM_EXIT n=$n simrc=$rc holdoff=${HOLD}s" | tee -a "$RUN_DIR/sim.log" >> "$RUN_DIR/ioc.raw.log"
  sleep "$HOLD"
done
