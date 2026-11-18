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

Example (IOC host 192.168.0.6, PLC 192.168.0.9):
  ads_stall_proxy.py --listen 192.168.0.6:48898 --target 192.168.0.9:48898 --src 192.168.0.6 \
      --stalls 1000,2000,3000 --repeats 3 --first-delay 90 --gap 150 --log runs/X/proxy.log
"""
import argparse, asyncio, signal, sys, time


def now():
    t = time.time()
    return time.strftime('%H:%M:%S', time.localtime(t)) + '.%03d' % int((t % 1) * 1000)


class Proxy:
    def __init__(self, a):
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
        try:
            while True:
                await self.gate.wait()
                data = await r.read(65536)
                if not data:
                    break
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
        except (ConnectionError, asyncio.CancelledError, OSError):
            pass
        finally:
            try:
                w.close()
            except Exception:
                pass

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
    p.add_argument('--log', default='')
    a = p.parse_args()
    a.th, a.tp = a.target.rsplit(':', 1)
    a.tp = int(a.tp)
    try:
        asyncio.run(Proxy(a).run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
