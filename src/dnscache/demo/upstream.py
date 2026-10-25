"""Where cache misses and prefetches are sent.

* ``RealUpstream``: real DNS over UDP to a real resolver (your network's DNS
  server, or 8.8.8.8 / 1.1.1.1). Latency is measured with a stopwatch.
* ``OfflineUpstream``: no network; invents answers with a realistic TTL and
  latency. Only for when DNS is blocked. Clearly labelled in every output.
"""
from __future__ import annotations

import platform
import re
import socket
import subprocess
import time
import zlib
from dataclasses import dataclass

from ..collect import dnsmsg
from ..sim.environment import Upstream as LatencyModel


@dataclass
class Answer:
    response: bytes | None
    addresses: list[str]
    ttl: int
    ms: float
    rcode: str = "NOERROR"


RCODES = {0: "NOERROR", 2: "SERVFAIL", 3: "NXDOMAIN", 5: "REFUSED"}


def system_dns_servers() -> list[str]:
    """Best effort: the DNS servers this computer is configured to use."""
    out = []
    try:
        if platform.system() == "Windows":
            txt = subprocess.run(["ipconfig", "/all"], capture_output=True, text=True, timeout=5).stdout
            grab = False
            for line in txt.splitlines():
                if "DNS Servers" in line:
                    grab = True
                    line = line.split(":", 1)[1]
                elif grab and not re.match(r"^\s+\d", line):
                    grab = False
                if grab:
                    out += re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", line)
        else:
            with open("/etc/resolv.conf") as f:
                out = re.findall(r"^nameserver\s+(\d{1,3}(?:\.\d{1,3}){3})", f.read(), re.M)
    except Exception:
        pass
    return [ip for ip in dict.fromkeys(out) if not ip.startswith("127.")]


class RealUpstream:
    label = "real"

    def __init__(self, server: str, port: int = 53, timeout: float = 2.0):
        self.server, self.port, self.timeout = server, port, timeout
        self.queries = 0

    def __str__(self):
        return f"{self.server}:{self.port}" if self.port != 53 else self.server

    def fetch(self, name: str, qtype: str = "A") -> Answer:
        pkt = dnsmsg.build_query(name, qtype)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self.timeout)
            t0 = time.perf_counter()
            s.sendto(pkt, (self.server, self.port))
            while True:
                resp, _ = s.recvfrom(4096)
                if resp[:2] == pkt[:2]:
                    break
            ms = (time.perf_counter() - t0) * 1000
        self.queries += 1
        rcode = RCODES.get(resp[3] & 0x0F, str(resp[3] & 0x0F))
        addrs = [d for _, t, _, d in dnsmsg.parse_answers(resp) if t in ("A", "AAAA")]
        return Answer(resp, addrs, dnsmsg.min_ttl(resp), ms, rcode)

    def works(self) -> bool:
        try:
            a = self.fetch("example.com")
            return a.rcode == "NOERROR" and bool(a.addresses)
        except OSError:
            return False


class OfflineUpstream:
    label = "offline (simulated upstream)"

    def __init__(self, seed: int = 1):
        self.model = LatencyModel({}, seed=seed, median_ms=35.0)
        self.queries = 0
        self._n = 0

    def __str__(self):
        return "offline (simulated)"

    def fetch(self, name: str, qtype: str = "A") -> Answer:
        self.queries += 1
        self._n += 1
        h = zlib.crc32(name.encode())
        ttl = [60, 300, 300, 3600, 120, 600][h % 6]
        ms = self.model.latency_ms(name, self._n)
        ip = f"10.{h >> 16 & 255}.{h >> 8 & 255}.{h & 255}"
        return Answer(None, [ip], ttl, ms)


def choose_upstream(server: str | None = None, offline: bool = False, log=print):
    if offline:
        return OfflineUpstream()
    candidates = [server] if server else system_dns_servers() + ["8.8.8.8", "1.1.1.1"]
    for ip in candidates:
        host, _, port = ip.partition(":")
        up = RealUpstream(host, int(port or 53))
        if up.works():
            return up
        log(f"  DNS server {ip} did not answer, trying the next one...")
    raise SystemExit("No DNS server answered. Check the internet connection, pass --upstream <ip>, "
                     "or run with --offline to use the simulated upstream.")
