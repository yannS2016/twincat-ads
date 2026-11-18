#!/usr/bin/env python3
"""Log timestamped PV updates and connection/alarm state. Separates 'IOC process alive' from 'IOC serving live data'.
Usage: watch_pvs.py <outfile.csv> [pv ...]"""
import sys, time, epics
out = open(sys.argv[1], "a", buffering=1)
pvs = sys.argv[2:] or ["PLC:TST:LCLSGeneral:LogMessageCount_RBV", "PLC:TST:AccumulatedFastFaults_RBV", "TST:SM:M1.RBV", "TST:SM:M1.SEVR", "TST:SM:M2.RBV"]
def now(): return time.strftime("%H:%M:%S") + ".%03d" % (int(time.time() * 1000) % 1000)
def cb(pvname=None, value=None, severity=None, status=None, **kw):
    out.write(f"{now()},{pvname},value,{value},{severity},{status}\n")
def conn(pvname=None, conn=None, **kw):
    out.write(f"{now()},{pvname},{'connected' if conn else 'DISCONNECTED'},,,\n")
objs = [epics.PV(p, callback=cb, connection_callback=conn, auto_monitor=True) for p in pvs]
out.write(f"{now()},watcher,start,{len(pvs)} pvs,,\n")
while True: time.sleep(1)
