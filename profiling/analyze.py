#!/usr/bin/env python3 -I
"""Parse an IOC log (raw run log or legacy ioc-*.log) into an event timeline and summary.
Usage: analyze.py <logfile> [--csv out.csv]
Handles three timestamp styles: asyn '2026/11/16 15:29:28.498', run_scenario prefix 'HH:MM:SS.mmm ', and 'PROF HH:MM:SS.mmm TAG'.
Unstamped lines (legacy 'Sum read N failed') inherit the previous timestamp (marked ~)."""
import re, sys, csv
from collections import Counter

RE_ASYN = re.compile(r'(\d{4}/\d\d/\d\d )?(\d\d:\d\d:\d\d\.\d{3})')
RE_PROF = re.compile(r'PROF (\d\d:\d\d:\d\d\.\d{3}) (\w+) ?(.*)')
RE_SUMFAIL = re.compile(r'Sum read\s*(\d+) failed: status (\d+)')
EVENTS = [  # (regex, tag)
    (re.compile(r'connection lost for port (\d+)'), 'CONN_LOST'),
    (re.compile(r'No connection! Try to reconnect'), 'NO_CONN'),
    (re.compile(r'state change: "(\w+ ?\w*)" -> "(\w+ ?\w*)"'), 'STATE'),
    (re.compile(r'exiting ioc'), 'EXIT'),
    (re.compile(r'(\d+)/(\d+) handles resolved'), 'HANDLES_DONE'),
    (re.compile(r'complete — (\d+) resolved, (\d+) failed'), 'SYMINFO_DONE'),
    (re.compile(r'SUMUP failed rc=(0x[0-9a-f]+)'), 'RESOLVE_CHUNK_FAIL'),
    (re.compile(r'InvokeId mismatch|No response pending'), 'ADSLIB_WARN'),
    (re.compile(r'iocRun: All initialization complete'), 'IOC_RUN'),
    (re.compile(r'SIM_(START|EXIT) (.*)'), 'SIM'),
    (re.compile(r'IOC_RC=(\d+)'), 'IOC_RC'),
]
ERR = {1861: 'SYNCTIMEOUT(0x745)', 1797: '0x705'}

def secs(t):
    h, m, s = t.split(':'); return int(h) * 3600 + int(m) * 60 + float(s)

def parse(path):
    ev, last, approx = [], None, False
    for line in open(path, errors='replace'):
        line = line.rstrip('\n')
        m = RE_PROF.search(line)
        if m:
            last = secs(m.group(1)); ev.append((last, False, m.group(2), m.group(3))); continue
        m = RE_ASYN.search(line)
        if m:
            last = secs(m.group(2))
        for sm in RE_SUMFAIL.finditer(line):
            st = int(sm.group(2))
            ev.append((last, last is None or not m, 'BULK_FAIL', f'grp={sm.group(1)} status={st}'))
        if RE_SUMFAIL.search(line) and 'PROF' not in line:
            continue
        for rx, tag in EVENTS:
            mm = rx.search(line)
            if mm:
                ev.append((last, False, tag, ' '.join(mm.groups()) if mm.groups() else '')); break
    ev = [e for e in ev if e[0] is not None]
    # normalize and drop asyn/PROF duplicates of the same event (same tag within 1 s, keep the PROF line)
    ev = [(t, ap, 'EXIT' if tag == 'EXIT_NEG1' else tag, info) for t, ap, tag, info in ev]
    dd = []
    for e in ev:
        if e[2] in ('CONN_LOST', 'HANDLES_DONE', 'EXIT') and any(d[2] == e[2] and abs(d[0] - e[0]) < 1.0 for d in dd[-6:]):
            continue
        dd.append(e)
    ev = dd
    # A backward time jump of >60 s means a new IOC session (or midnight wrap): keep the timeline monotonic.
    out, base, prev = [], 0.0, None
    for t, ap, tag, info in ev:
        if prev is not None and t < prev - 60:
            base += prev - t + 1.0
            out.append((t + base, False, 'SESSION_BREAK', 'time went backwards (new IOC session or midnight)'))
        out.append((t + base, ap, tag, info)); prev = t
    return out

def main():
    path = sys.argv[1]
    ev = parse(path)
    if not ev: print('no events'); return
    t0 = ev[0][0]
    print(f'== timeline ({path}), t0={t0:.3f}s; BULK_FAIL runs collapsed ==')
    rows, i = [], 0
    while i < len(ev):
        t, approx, tag, info = ev[i]
        if tag == 'BULK_FAIL':
            j = i; sts = Counter()
            while j < len(ev) and ev[j][2] == 'BULK_FAIL':
                sts[re.sub(r'.*status=(-?\d+).*', r'\1', ev[j][3])] += 1; j += 1
            n = j - i
            dur = ev[j-1][0] - t
            info = f'{n} failures over {dur:.1f}s ' + ' '.join(f'status{k}x{v}' for k, v in sts.items())
            rows.append((t, f'{"~" if approx else ""}', 'BULK_FAIL_RUN', info)); i = j; continue
        rows.append((t, '~' if approx else '', tag, info)); i += 1
    for t, ap, tag, info in rows:
        print(f'+{t - t0:9.3f}s {ap}{tag:18s} {info}')
    fails = [e for e in ev if e[2] == 'BULK_FAIL']
    lost = [e for e in ev if e[2] == 'CONN_LOST']
    print('\n== summary ==')
    print(f'bulk failures: {len(fails)}', end='')
    if fails: print(f' (first +{fails[0][0]-t0:.1f}s, last +{fails[-1][0]-t0:.1f}s, span {fails[-1][0]-fails[0][0]:.1f}s)')
    else: print()
    print('status codes:', dict(Counter(re.sub(r'.*status=(-?\d+).*', r'\1', e[3]) for e in fails)))
    print(f'connection losses: {len(lost)}; ioc exit(-1): {sum(e[2] == "EXIT" for e in ev)}')
    starts = [e for e in ev if e[2] == 'SIM' and e[3].startswith('START')]
    if starts: print(f'procServ-sim IOC starts: {len(starts)}')
    hd = [e for e in ev if e[2] == 'HANDLES_DONE']
    print('handle resolution:', [e[3] for e in hd] or 'none logged')
    if fails and lost:
        later = [e for e in ev if e[2] == 'STATE' and 'ADSSTATE_RUN' in e[3].split('->')[-1] if e[0] > lost[0][0]] if False else []
    if len(sys.argv) > 3 and sys.argv[2] == '--csv':
        with open(sys.argv[3], 'w', newline='') as f:
            w = csv.writer(f); w.writerow(['t_rel_s', 'approx', 'tag', 'info'])
            for t, ap, tag, info in rows: w.writerow([f'{t-t0:.3f}', ap, tag, info])

main()
