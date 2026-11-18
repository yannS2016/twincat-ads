#!/usr/bin/env python3 -I
"""Classify one stall trial. Usage: classify.py <run_dir> [observe_s=60]
Outcome: NOEFFECT (no bulk failure), RECOVERED (failures, then clean), STUCK (bulk failing at the end, IOC alive), EXIT (IOC exited), SIGPIPE.
Prints one CSV line: outcome,stall_planned_ms,first_fail_after_start_s,n_bulk_fail,n_1797,ioc_rc,exit_after_start_s"""
import re, sys, os
d = sys.argv[1]; obs = float(sys.argv[2]) if len(sys.argv) > 2 else 60
def t(x):
    h, m, s = x.split(':'); return int(h) * 3600 + int(m) * 60 + float(s)
px = open(os.path.join(d, 'proxy.log')).read()
ms = re.search(r'STALL_START stall=\d+ planned_ms=(\d+)', px)
ts = re.search(r'^(\S+) PROXY STALL_START', px, re.M); te = re.search(r'^(\S+) PROXY STALL_END', px, re.M)
if not ts: print('NOSTALL,,,,,,'); sys.exit()
t0 = t(ts.group(1)); t1 = t(te.group(1)) if te else t0
fails, f1797, rc, ext, last_fail = [], 0, '', '', None
for line in open(os.path.join(d, 'ioc.raw.log'), errors='replace'):
    m = re.search(r'PROF (\d\d:\d\d:\d\d\.\d+) BULK_FAIL .*status=(-?\d+)', line)
    if m:
        tt = t(m.group(1))
        if tt >= t0:
            fails.append(tt)
            if m.group(2) == '1797': f1797 += 1
            last_fail = tt
    m = re.search(r'^(\d\d:\d\d:\d\d\.\d+) IOC_RC=(\d+)', line)
    if m and t(m.group(1)) >= t0 and m.group(2) not in ('143', '129'): rc = m.group(2); ext = round(t(m.group(1)) - t0, 1)  # 143/129: our own stop
desync = len(re.findall(r'Frame to short|is out of range', open(os.path.join(d, 'ioc.raw.log'), errors='replace').read()))
skips = len(re.findall(r'ReceiveFrame timeout: \d+ bytes already consumed', open(os.path.join(d, 'ioc.raw.log'), errors='replace').read()))
end = t1 + obs
if rc == '141': out = 'SIGPIPE'
elif rc: out = 'EXIT'
elif not fails: out = 'NOEFFECT'
elif last_fail is not None and last_fail > end - 20: out = 'STUCK'
else: out = 'RECOVERED'
print(f'{out},{ms.group(1)},{round(fails[0]-t0,1) if fails else ""},{len(fails)},{f1797},{rc},{ext},desync={desync},f12skips={skips}')
