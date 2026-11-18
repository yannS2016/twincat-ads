#!/usr/bin/env python3
"""TCP proxy that stalls an ADS connection on a schedule, keeping both TCP sessions open.

The IOC connects to the proxy; the proxy connects to the PLC from the IOC host's own address, so the PLC still sees
the IOC host address (AMS route unchanged). During a stall nothing is forwarded in either direction and nothing is
read from the sockets; data waits in the kernel buffers and is delivered when the stall ends. This models a network
stall (delay without loss), the case a cable pull cannot produce.

  --mode pause   hold traffic for the stall time, then release (default)
  --mode cut     close both sockets at the stall time (RST-like, same effect as a cable pull)
  --mode split   at the stall time, deliver only the first part of the next PLC response chunk, then hold the
                 rest for the stall time (a stall that starts in the middle of an AMS frame)

Events are logged with host wall-clock time to stdout and --log.
SIGUSR1 triggers one immediate stall of --stall-ms (useful by hand).

--inject-reset adds a frame-aware path (not combinable with --mode split) and lets SIGUSR2, or --inject-delay seconds
after the IOC's first frame, send one ADS Write Control (AdsState 2 = RESET, the "Restart TwinCAT System in Run Mode"
command XAE sends to port 10000) to the PLC on the IOC's own connection and AMS Net ID, so no extra AMS route is
needed. The PLC's reply is dropped before it reaches the IOC.
--probe-s N then sends an ADS Read State request to port 851 every 100 ms for N seconds after the Reset and logs
PROBE_STATE (ADS state or error, on change) and PROBE_SILENT/PROBE_RESUMED, to measure how long the PLC runtime is
away. The connection must survive for this, so run an IOC build that does not exit (noexit variants).

Example (IOC host 192.168.0.6, PLC 192.168.0.9):
  ads_stall_proxy.py --listen 192.168.0.6:48898 --target 192.168.0.9:48898 --src 192.168.0.6 \
      --stalls 1000,2000,3000 --repeats 3 --first-delay 90 --gap 150 --log runs/X/proxy.log
"""
import argparse, asyncio, signal, struct, sys, time

INJECT_INVOKE = 0x5E5E0001
PROBE_BASE = 0x5E5F0000


def now():
    t = time.time()
    return time.strftime('%H:%M:%S', time.localtime(t)) + '.%03d' % int((t % 1) * 1000)


class FrameBuf:
    """Splits an AMS/TCP byte stream into complete frames (6-byte header: 2 reserved, 4 length)."""

    def __init__(self):
        self.buf = b''

    def feed(self, data):
        self.buf += data
        out = []
        while len(self.buf) >= 6:
            n = 6 + struct.unpack_from('<I', self.buf, 2)[0]
            if len(self.buf) < n:
                break
            out.append(self.buf[:n])
            self.buf = self.buf[n:]
        return out


class Proxy:
    def __init__(self, a):
        self.info = {}  # (client writer, plc writer) -> (target net id, source net id, source port) learned from the IOC
        self.a = a
        self.gate = None  # asyncio objects are created in run(), inside the running loop (Python 3.9)
        self.conns = []
        self.log_f = open(a.log, 'a', buffering=1) if a.log else None
        self.first_conn = None
        self.split_pending = False
        self.split_done = None
        self.stats = {'bytes_c2p': 0, 'bytes_p2c': 0}

    def log(self, ev, detail=''):
        line = f'{now()} PROXY {ev} {detail}'.rstrip()
        print(line, flush=True)
        if self.log_f:
            self.log_f.write(line + '\n')

    async def pump(self, r, w, key, name):
        last = time.monotonic()
        try:
            while True:
                await self.gate.wait()
                data = await r.read(65536)
                if not data:
                    self.log('EOF', f'dir={name} (peer closed the connection)')
                    break
                t = time.monotonic()
                if t - last > 1.0 and self.gate.is_set():
                    # silence on the wire, seen when traffic resumes (no PLC data for this long)
                    self.log('GAP', f'dir={name} silent_ms={int((t - last) * 1000)}')
                last = t
                if self.split_pending and name == 'p2c' and len(data) > 200:
                    # deliver a partial frame, then close the gate (stall starts mid-frame)
                    cut = 80 + (len(data) % 120)  # past the AMS/AoE headers (about 46 bytes), inside the response payload
                    self.split_pending = False
                    w.write(data[:cut]); await w.drain()
                    self.stats[key] += cut
                    self.gate.clear()
                    self.log('SPLIT', f'delivered {cut} of {len(data)} bytes, holding the rest')
                    self.split_done.set()
                    await self.gate.wait()
                    data = data[cut:]
                await self.gate.wait()
                w.write(data)
                await w.drain()
                self.stats[key] += len(data)
        except (ConnectionError, asyncio.CancelledError, OSError) as e:
            if not isinstance(e, asyncio.CancelledError):
                self.log('ERR', f'dir={name} {type(e).__name__}: {e}')
        finally:
            try:
                w.close()
            except Exception:
                pass

    async def pump_framed(self, r, w, key, name, conn):
        fb = FrameBuf()
        try:
            while True:
                await self.gate.wait()
                data = await r.read(65536)
                if not data:
                    self.log('EOF', f'dir={name} (peer closed the connection)')
                    break
                for f in fb.feed(data):
                    if len(f) >= 38 and f[:2] == b'\0\0':
                        if name == 'c2p' and conn not in self.info:
                            self.info[conn] = (f[6:12], f[14:20], struct.unpack_from('<H', f, 20)[0])
                        inv = struct.unpack_from('<I', f, 34)[0] if name == 'p2c' else 0
                        if inv == INJECT_INVOKE:
                            err = struct.unpack_from('<I', f, 30)[0]
                            res = struct.unpack_from('<I', f, 38)[0] if len(f) >= 42 else None
                            self.log('INJECT_REPLY', f'ams_error={err} result={res}')
                            continue
                        if inv & 0xFFFF0000 == PROBE_BASE:
                            self.probe_reply(f)
                            continue
                    w.write(f)
                    self.stats[key] += len(f)
                await w.drain()
        except (ConnectionError, asyncio.CancelledError, OSError) as e:
            if not isinstance(e, asyncio.CancelledError):
                self.log('ERR', f'dir={name} {type(e).__name__}: {e}')
        finally:
            try:
                w.close()
            except Exception:
                pass

    async def inject_reset(self):
        for conn in list(self.conns):
            if conn in self.info:
                target, src, sport = self.info[conn]
                payload = struct.pack('<HHI', 2, 0, 0)  # AdsState RESET, DeviceState 0, no data
                ams = (target + struct.pack('<H', 10000) + src + struct.pack('<H', sport) +
                       struct.pack('<HHIII', 5, 4, len(payload), 0, INJECT_INVOKE) + payload)
                conn[1].write(struct.pack('<HI', 0, len(ams)) + ams)
                self.log('INJECT_RESET', f'write control state=2 to port 10000, src_port={sport}')
                if self.a.probe_s > 0:
                    asyncio.ensure_future(self.probe_loop(conn, self.a.probe_s))
                return
        self.log('INJECT_SKIPPED', 'no learned connection')

    def probe_reply(self, f):
        t = time.monotonic()
        err = struct.unpack_from('<I', f, 30)[0]
        res, state = (struct.unpack_from('<IH', f, 38) if len(f) >= 44 else (None, None))
        if self.probe_silent_since is not None:
            self.log('PROBE_RESUMED', f'after {int((t - self.probe_silent_since) * 1000)} ms without a reply')
            self.probe_silent_since = None
        self.probe_last_reply = t
        key = (err, res, state)
        if key != self.probe_last_key:
            self.probe_last_key = key
            self.log('PROBE_STATE', f't=+{t - self.probe_t0:.2f}s ams_error={err} result={res} ads_state={state}')

    async def probe_loop(self, conn, secs):
        target, src, sport = self.info[conn]
        self.probe_t0 = self.probe_last_reply = time.monotonic()
        self.probe_last_key = None
        self.probe_silent_since = None
        k = 0
        while time.monotonic() - self.probe_t0 < secs and conn in self.conns:
            k = (k + 1) & 0xFFFF
            ams = (target + struct.pack('<H', 851) + src + struct.pack('<H', sport) +
                   struct.pack('<HHIII', 4, 4, 0, 0, PROBE_BASE | k))
            conn[1].write(struct.pack('<HI', 0, len(ams)) + ams)
            await asyncio.sleep(0.1)
            if self.probe_silent_since is None and time.monotonic() - self.probe_last_reply > 1.0:
                self.probe_silent_since = self.probe_last_reply
                self.log('PROBE_SILENT', f't=+{time.monotonic() - self.probe_t0:.2f}s no reply for over 1 s')
        self.log('PROBE_END', f'conn_alive={conn in self.conns}')

    async def inject_later(self):
        await self.first_conn.wait()
        await asyncio.sleep(self.a.inject_delay)
        await self.inject_reset()

    async def handle(self, cr, cw):
        peer = cw.get_extra_info('peername')
        try:
            local = (self.a.src, 0) if self.a.src else None
            pr, pw = await asyncio.open_connection(self.a.th, self.a.tp, local_addr=local)
        except OSError as e:
            self.log('TARGET_CONNECT_FAIL', str(e))
            cw.close()
            return
        self.log('CONN_OPEN', f'client={peer[0]}:{peer[1]} target={self.a.th}:{self.a.tp}')
        self.conns.append((cw, pw))
        self.first_conn.set()
        if self.a.inject_reset:
            t1 = asyncio.ensure_future(self.pump_framed(cr, pw, 'bytes_c2p', 'c2p', (cw, pw)))
            t2 = asyncio.ensure_future(self.pump_framed(pr, cw, 'bytes_p2c', 'p2c', (cw, pw)))
        else:
            t1 = asyncio.ensure_future(self.pump(cr, pw, 'bytes_c2p', 'c2p'))
            t2 = asyncio.ensure_future(self.pump(pr, cw, 'bytes_p2c', 'p2c'))
        await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
        for t in (t1, t2):
            t.cancel()
        for w in (cw, pw):
            try:
                w.close()
            except Exception:
                pass
        if (cw, pw) in self.conns:
            self.conns.remove((cw, pw))
        self.info.pop((cw, pw), None)
        self.log('CONN_CLOSE', f'client={peer[0]}:{peer[1]}')

    async def stall(self, ms, n):
        if self.a.mode == 'cut':
            # STALL_START/END are logged so classify.py and ladder.sh treat a cut like a stall of length 0
            self.log('STALL_START', f'stall={n} planned_ms=0 conns={len(self.conns)}')
            self.log('CUT', f'stall={n} closing {len(self.conns)} connection(s)')
            for cw, pw in list(self.conns):
                for w in (cw, pw):
                    try:
                        w.transport.abort()
                    except Exception:
                        pass
            self.log('STALL_END', f'stall={n} held_ms=0')
            return
        if self.a.mode == 'split':
            self.split_done.clear()
            self.split_pending = True
            try:
                await asyncio.wait_for(self.split_done.wait(), 10)
            except asyncio.TimeoutError:
                self.split_pending = False
                self.log('SPLIT_TIMEOUT', 'no PLC chunk to split in 10 s')
                return
        else:
            self.gate.clear()
        t0 = time.monotonic()
        self.log('STALL_START', f'stall={n} planned_ms={ms} conns={len(self.conns)}')
        await asyncio.sleep(ms / 1000)
        self.gate.set()
        self.log('STALL_END', f'stall={n} held_ms={int((time.monotonic() - t0) * 1000)} '
                 f'bytes_c2p={self.stats["bytes_c2p"]} bytes_p2c={self.stats["bytes_p2c"]}')

    async def schedule(self):
        stalls = [int(x) for x in self.a.stalls.split(',') if x] if self.a.stalls else []
        plan = [ms for ms in stalls for _ in range(self.a.repeats)]
        if not plan:
            return
        await self.first_conn.wait()
        self.log('SCHEDULE', f'plan_ms={plan} first_delay={self.a.first_delay}s gap={self.a.gap}s')
        await asyncio.sleep(self.a.first_delay)
        for n, ms in enumerate(plan, 1):
            await self.stall(ms, n)
            if n < len(plan):
                await asyncio.sleep(self.a.gap)
        self.log('SCHEDULE_DONE')

    async def run(self):
        self.gate = asyncio.Event()
        self.gate.set()
        self.first_conn = asyncio.Event()
        self.split_done = asyncio.Event()
        host, port = self.a.listen.rsplit(':', 1)
        srv = await asyncio.start_server(self.handle, host, int(port))
        self.log('LISTEN', f'{self.a.listen} -> {self.a.th}:{self.a.tp} mode={self.a.mode}')
        loop = asyncio.get_running_loop()
        counter = [1000]

        def manual():
            counter[0] += 1
            asyncio.ensure_future(self.stall(self.a.stall_ms, counter[0]))

        loop.add_signal_handler(signal.SIGUSR1, manual)
        if self.a.inject_reset:
            loop.add_signal_handler(signal.SIGUSR2, lambda: asyncio.ensure_future(self.inject_reset()))
            if self.a.inject_delay >= 0:
                asyncio.ensure_future(self.inject_later())
        asyncio.ensure_future(self.schedule())
        async with srv:
            await srv.serve_forever()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--listen', required=True, help='host:port the IOC connects to')
    p.add_argument('--target', required=True, help='host:port of the PLC')
    p.add_argument('--src', default='', help='local address for the PLC-side connection (IOC host address)')
    p.add_argument('--mode', choices=['pause', 'cut', 'split'], default='pause')
    p.add_argument('--stalls', default='', help='comma list of stall durations in ms')
    p.add_argument('--repeats', type=int, default=1)
    p.add_argument('--first-delay', type=float, default=90, help='seconds after the first connection')
    p.add_argument('--gap', type=float, default=150, help='seconds between stalls')
    p.add_argument('--stall-ms', type=int, default=2000, help='duration for SIGUSR1 stalls')
    p.add_argument('--inject-reset', action='store_true', help='enable frame-aware mode and the Reset injection')
    p.add_argument('--inject-delay', type=float, default=-1, help='send the Reset this many seconds after the first connection (default: only on SIGUSR2)')
    p.add_argument('--probe-s', type=float, default=0, help='probe PLC state on port 851 for this many seconds after the Reset')
    p.add_argument('--log', default='')
    a = p.parse_args()
    if a.inject_reset and a.mode == 'split':
        p.error('--inject-reset cannot be combined with --mode split')
    a.th, a.tp = a.target.rsplit(':', 1)
    a.tp = int(a.tp)
    try:
        asyncio.run(Proxy(a).run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
