#!/bin/bash
# Boot cycler: restarts the IOC every CYCLE_S seconds (default 50) so that a PLC event lands in an IOC boot window
# (handles resolved, cyclic state poll not yet started: roughly +2 s .. +45 s after start with ~7.4k symbols).
# Each cycle is killed (SIGTERM, rc 143) just after the first state poll. A PLC-triggered exit shows a different IOC_RC.
# Usage: cycler.sh <scenario> [cycle_s=50]    Stop with Ctrl-C / kill the script.
HERE=$(cd "$(dirname "$0")" && pwd)
SCEN=${1:?scenario}; CYC=${2:-50}
export RUN_DIR=$HERE/runs/$(date +%Y%m%d-%H%M%S)-$SCEN
mkdir -p "$RUN_DIR"; echo "run dir: $RUN_DIR" >&2; echo "cycle=$CYC" > "$RUN_DIR/meta.txt"
trap 'kill -TERM -- -$PG 2>/dev/null; echo "$(date +%H:%M:%S.%3N) CYCLER_STOP" >> "$RUN_DIR/sim.log"; exit 0' INT TERM
n=0
while true; do
  n=$((n+1)); echo "$(date +%H:%M:%S.%3N) SIM_START n=$n" | tee -a "$RUN_DIR/sim.log" >> "$RUN_DIR/ioc.raw.log"
  setsid "$HERE/run_scenario.sh" "$SCEN" > /dev/null 2>&1 < /dev/null & PG=$!
  for ((t=0; t<CYC; t++)); do sleep 1; kill -0 $PG 2>/dev/null || break; done
  if kill -0 $PG 2>/dev/null; then
    echo "$(date +%H:%M:%S.%3N) CYCLE_KILL n=$n" | tee -a "$RUN_DIR/sim.log" >> "$RUN_DIR/ioc.raw.log"
    kill -TERM -- -$PG 2>/dev/null
  fi
  wait $PG 2>/dev/null
  echo "$(date +%H:%M:%S.%3N) SIM_EXIT n=$n" | tee -a "$RUN_DIR/sim.log" >> "$RUN_DIR/ioc.raw.log"
  sleep 2
done
