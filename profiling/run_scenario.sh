#!/bin/bash
# Usage: run_scenario.sh <scenario-name> [--shipped]
# Starts the sample IOC with raw (non-deduped) logging into runs/<ts>-<scenario>/.
# Default binary: instrumented build (ads-ioc-prof). --shipped uses ~/ads-ioc/R2.2.1 for A/B comparison.
set -e
SCEN=${1:?scenario name}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=${REPO:-$HOME/lcls-plc-motion-abstraction-sample}
# Env IOC_NAME (iocBoot dir and IOC name) and DB_BASE (database and archive file base name) select another project, e.g.
# REPO=~/modules/lcls-plc-tst-tcads IOC_NAME=ioc-TestIOC DB_BASE=TestIOC
NAME=${IOC_NAME:-ioc-motion-abstraction-sample}
DB_BASE=${DB_BASE:-motion_abstraction_sample}
SRC=$REPO/iocBoot/$NAME
ADS=${ADS_IOC_DIR:-$HERE/ads-ioc-prof}
[ "$2" = "--shipped" ] && ADS=$HOME/ads-ioc/R2.2.1
RUN=${RUN_DIR:-$HERE/runs/$(date +%Y%m%d-%H%M%S)-$SCEN}
STAGE=$RUN/stage
mkdir -p "$STAGE/data"
for f in st.cmd $DB_BASE.db $DB_BASE.archive; do cp -f "$SRC/$f" "$STAGE/$f"; done
# Profiling only: raise asyn trace to ERROR+INFO (0x71 floods: 127k lines per 11 min) on the staged copy (repo untouched)
sed -i 's/asynSetTraceMask("\$(ASYN_PORT)", -1, 0x41)/asynSetTraceMask("$(ASYN_PORT)", -1, 0x41)/' "$STAGE/st.cmd"
# Profiling only: point the IOC at a stall proxy (the AMSID stays the PLC's) when IOC_IPADDR is set
[ -n "$IOC_IPADDR" ] && sed -i -E "s/(epicsEnvSet\\(\"IPADDR\",[[:space:]]*)\"[0-9.]+\"/\\1\"$IOC_IPADDR\"/" "$STAGE/st.cmd"
sed -e "s/^epicsEnvSet(\"IOC\",.*)/epicsEnvSet(\"IOC\",\"\${IOC=$NAME}\")/" "$ADS/iocBoot/templates/envPaths" > "$STAGE/envPaths"
echo "epicsEnvSet(\"IOC_TOP\", \"$STAGE\")"        >> "$STAGE/envPaths"
echo "epicsEnvSet(\"IOC_DATA\", \"$STAGE/data\")" >> "$STAGE/envPaths"
{
  echo "scenario=$SCEN"; echo "start=$(date -Is)"; echo "ioc_bin=$ADS/bin/rhel9-x86_64/adsIoc"
  echo "ioc_bin_md5=$(md5sum $ADS/bin/rhel9-x86_64/adsIoc | cut -d' ' -f1)"
  grep -E 'IPADDR|AMSID|AMS_PORT|ADS_TIMEOUT' "$STAGE/st.cmd" | grep epicsEnvSet
  echo "repo_head=$(git -C $REPO rev-parse --short HEAD)"
} >> "$RUN/meta.txt"
export IOC_COMMON=${IOC_COMMON:-/reg/d/iocCommon} EPICS_HOST_ARCH=rhel9-x86_64
cd "$STAGE"
echo "run dir: $RUN" >&2
# stamp every raw line with wall-clock ms so unstamped printf/AdsLib lines can be placed on the timeline
tail -f /dev/null | "$ADS/bin/rhel9-x86_64/adsIoc" st.cmd 2>&1 \
  | while IFS= read -r l; do printf '%s %s\n' "$(date +%H:%M:%S.%3N)" "$l"; done | tee -a "$RUN/ioc.raw.log"
# exit status of adsIoc itself (pipeline slot 1): 141=SIGPIPE, 139=SIGSEGV, 255=exit(-1), 134=abort
RC=${PIPESTATUS[1]}
echo "$(date +%H:%M:%S.%3N) IOC_RC=$RC" | tee -a "$RUN/ioc.raw.log" >> "$RUN/sim.log"
exit $RC
