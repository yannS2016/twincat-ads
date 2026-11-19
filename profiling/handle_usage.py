#!/usr/bin/env python3
"""Read the PLC's symbol and dynamic symbol handle usage (read-only). Needs pyads.

  handle_usage.py --plc 172.21.148.154 --local-net-id 134.79.165.33.1.1 [--count 3 --interval 20]

Uses ADS Read on index group 0xF00F (symbol upload info 2, 24 bytes of uint32: nSymbols, nSymSize, nDatatypes,
nDatatypeSize, nMaxDynSymbols, nUsedDynSymbols). A ReadWrite on the same group is rejected (error 1793).
Stop the IOC first when --local-net-id is the IOC's own AMS Net ID, or the two evict each other.
"""
import argparse, struct, time
import pyads


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--plc', required=True, help='PLC IP address')
    p.add_argument('--net-id', default='', help='PLC AMS Net ID (default: <plc>.1.1)')
    p.add_argument('--local-net-id', required=True, help='AMS Net ID this host uses (needs a route on the PLC)')
    p.add_argument('--port', type=int, default=851)
    p.add_argument('--count', type=int, default=1)
    p.add_argument('--interval', type=float, default=10)
    a = p.parse_args()
    net = a.net_id or a.plc + '.1.1'
    pyads.set_local_address(a.local_net_id)
    pyads.add_route(net, a.plc)
    c = pyads.Connection(net, a.port, a.plc)
    c.set_timeout(3000)
    c.open()
    for i in range(a.count):
        v = struct.unpack('<6I', bytes(c.read(0xF00F, 0, pyads.PLCTYPE_BYTE * 24)))
        print(f'{time.strftime("%H:%M:%S")} state={c.read_state()[0]} symbols={v[0]} datatypes={v[2]} '
              f'max_dyn={v[4]} used_dyn={v[5]} free_dyn={v[4] - v[5]}', flush=True)
        if i < a.count - 1:
            time.sleep(a.interval)
    c.close()


if __name__ == '__main__':
    main()
