#!/bin/bash
# Build the profiling stack from the site twincat-ads module and an ads-ioc tree.
#   build-profiling-ioc.sh <variant> [out_dir]
#   variant: base  = instrumentation only (log lines, no behavior change)
#            f10   = base + F10 (restore connectedAds_)
#            f12   = base + F10 + F12 (AdsLib frame skip)
# Env: SITE_MODULE (twincat-ads module dir, default the R2.0.0-1.1.1 site install), ADS_IOC_SRC (built ads-ioc tree),
#      EPICS_SITE_TOP/BASE_MODULE_VERSION via RELEASE_SITE.example values below.
# Result: <out>/ads-<variant> (module) and <out>/ads-ioc-<variant> (IOC with bin/<arch>/adsIoc).
# Patches are module-relative (apply with -p1) and target twincat-ads R2.0.0-1.1.1 sources.
set -euo pipefail
VAR=${1:?variant base|f10|f12}
HERE=$(cd "$(dirname "$0")" && pwd)
OUT=${2:-$HERE/build}
SITE_MODULE=${SITE_MODULE:-/cds/group/pcds/epics/R7.0.3.1-2.0/modules/twincat-ads/R2.0.0-1.1.1}
ADS_IOC_SRC=${ADS_IOC_SRC:-$HOME/ads-ioc/R2.2.1}
ARCH=${EPICS_HOST_ARCH:-rhel9-x86_64}
case $VAR in base) P="0001";; f10) P="0001 0002";; f12) P="0001 0002 0003";; *) echo "bad variant"; exit 1;; esac
MOD=$OUT/ads-$VAR; IOC=$OUT/ads-ioc-$VAR
mkdir -p "$OUT"; rm -rf "$MOD" "$IOC"
cp -a "$SITE_MODULE" "$MOD"; chmod -R u+w "$MOD"
cp "$HERE/../RELEASE_SITE.example" "$MOD/RELEASE_SITE" 2>/dev/null || cat > "$MOD/RELEASE_SITE" <<EOS
EPICS_SITE_TOP = ${EPICS_SITE_TOP:-/cds/group/pcds/epics}
BASE_MODULE_VERSION = ${BASE_MODULE_VERSION:-R7.0.3.1-2.0}
EPICS_MODULES = \$(EPICS_SITE_TOP)/\$(BASE_MODULE_VERSION)/modules
EOS
for n in $P; do patch -d "$MOD" -p1 < "$HERE"/patches/$n-*.patch; done
make -C "$MOD" -j4 > "$OUT/build-$VAR-module.log" 2>&1
cp -a "$ADS_IOC_SRC" "$IOC"; chmod -R u+w "$IOC"
sed -i "s#^ADS=.*#ADS=$MOD#" "$IOC/configure/RELEASE"
OLD=$(cd "$ADS_IOC_SRC" && pwd)
{ grep -rIl "$OLD" "$IOC" 2>/dev/null || true; } | { grep -v "/O\.$ARCH" || true; } | xargs -r sed -i "s#$OLD#$IOC#g"
rm -f "$IOC/bin/$ARCH/adsIoc"; find "$IOC" -path "*/O.$ARCH" -type d -prune -exec rm -rf {} + 2>/dev/null || true
make -C "$IOC" -j4 > "$OUT/build-$VAR-ioc.log" 2>&1
ls -l "$IOC/bin/$ARCH/adsIoc"
echo "built variant=$VAR patches=[$P]  run with ADS_IOC_DIR=$IOC (see README)"
