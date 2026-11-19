#!/bin/bash
# Copy the committable part of profiling runs into an evidence directory.
#   trim-evidence.sh <dest_dir> <run_or_ladder_dir>...
# Run dir: keeps meta.txt, proxy.log, sim.log, pvs.csv and ioc.filtered.log.gz (PROF lines except BULK_ADD, AdsLib
# warnings and errors, desync and exit lines, IOC_RC). Ladder dir (has summary.csv): keeps meta.txt, progress.log, summary.csv.
# inject_trial.sh dirs (summary.csv plus r1, r2, ...) are handled recursively. The staged IOC files and the raw log are not copied.
set -euo pipefail
DEST=${1:?dest dir}; shift
mkdir -p "$DEST"
for d in "$@"; do
  d=${d%/}; n=$(basename "$d"); o=$DEST/$n; mkdir -p "$o"
  if [ -f "$d/summary.csv" ]; then
    cp "$d"/meta.txt "$d"/progress.log "$d"/summary.csv "$o"/
    # inject_trial.sh dirs keep one run dir per trial (r1, r2, ...)
    for sub in "$d"/r[0-9]*; do [ -d "$sub" ] && "$0" "$o" "$sub"; done
    continue
  fi
  for f in meta.txt proxy.log sim.log pvs.csv; do [ -f "$d/$f" ] && cp "$d/$f" "$o/"; done
  [ -f "$d/ioc.raw.log" ] || continue
  grep -aE "PROF |Warning:|Error|error|Frame to short|out of range|ReceiveFrame|InvokeId|No response|Broken pipe|Sum read|exiting|IOC_RC|SIM_" "$d/ioc.raw.log" \
    | grep -av "BULK_ADD" | gzip -9 > "$o/ioc.filtered.log.gz" || true
done
