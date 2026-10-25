"""Tiny DNS client for testing the forwarder:  python -m dnscache.collect.query example.com --port 5300"""
from __future__ import annotations

import argparse
import socket
import time

from . import dnsmsg


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--server", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5300)
    ap.add_argument("--type", default="A")
    a = ap.parse_args(argv)
    for name in a.names:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3)
            t0 = time.time()
            s.sendto(dnsmsg.build_query(name, a.type), (a.server, a.port))
            resp, _ = s.recvfrom(4096)
            ms = (time.time() - t0) * 1000
        ans = dnsmsg.parse_answers(resp)
        print(f"{name}: {ms:.1f} ms")
        for n, t, ttl, d in ans:
            print(f"   {n:40s} {t:6s} ttl={ttl:<6d} {d}")


if __name__ == "__main__":
    main()
