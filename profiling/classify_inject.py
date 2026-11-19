#!/usr/bin/env python3 -I
"""Classify one Reset-injection trial. Usage: classify_inject.py <run_dir> [observe_s=100]
Outcome: NODETECT (the IOC logged no INVALIDATE after the Reset: hung or missed it, the other outcomes do not apply),
NOEFFECT (IOC saw the restart and refreshed, no read failure after it), RECOVERED (failures, none in the last 20 s of the window),
STUCK (still failing at the end, IOC alive), EXIT (IOC exited, rc not 143/129), SIGPIPE (141).
Counts group failures (PROF BULK_FAIL) and per-symbol sub-read failures ('bulk read for ... failed') after the Reset.
Prints: outcome,reset_to_run_s,group_fail,sub_fail,ioc_rc,last_fail_after_reset_s,rehandle_calls"""
import os, re, sys

d = sys.argv[1]
obs = float(sys.argv[2]) if len(sys.argv) > 2 else 100


def t(x):
    h, m, s = x.split(':')
    return int(h) * 3600 + int(m) * 60 + float(s)


px = open(os.path.join(d, 'proxy.log')).read()
m = re.search(r'^(\S+) PROXY INJECT_RESET', px, re.M)
if not m:
    print('NORESET,,,,,,')
    sys.exit()
t0 = t(m.group(1))
# first probe reporting RUN (state 5) after the STOP
run_back = ''
seen_not_run = False
for ln in px.splitlines():
    mm = re.match(r'^(\S+) PROXY PROBE_STATE .*ads_state=(\w+)', ln)
    if not mm:
        continue
    if mm.group(2) != '5':
        seen_not_run = True
    elif seen_not_run:
        run_back = round(t(mm.group(1)) - t0, 1)
        break
group = sub = rehandle = invalidated = 0
last = None
rc = ''
for ln in open(os.path.join(d, 'ioc.raw.log'), errors='replace'):
    mm = re.match(r'^(\d\d:\d\d:\d\d\.\d+) ', ln)
    if not mm or t(mm.group(1)) < t0:
        continue
    ts = t(mm.group(1))
    if 'PROF' in ln and ' BULK_FAIL ' in ln:
        group += 1
        last = ts
    elif 'bulkReadThread: bulk read for' in ln and 'failed' in ln:
        sub += 1
        last = ts
    elif 'FIX_F15_REHANDLE' in ln:
        rehandle += 1
    elif ' INVALIDATE ' in ln:
        invalidated += 1
    r = re.search(r'IOC_RC=(\d+)', ln)
    if r and r.group(1) not in ('143', '129'):
        rc = r.group(1)
end = t0 + obs
if rc == '141':
    out = 'SIGPIPE'
elif rc:
    out = 'EXIT'
elif not invalidated:
    out = 'NODETECT'
elif last is None:
    out = 'NOEFFECT'
elif last > end - 20:
    out = 'STUCK'
else:
    out = 'RECOVERED'
print(f'{out},{run_back},{group},{sub},{rc},{round(last - t0, 1) if last else ""},{rehandle}')
