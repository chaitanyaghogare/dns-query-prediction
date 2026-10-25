"""Live caching DNS forwarder with logging and follow-up prefetching.

Point a client at it and every lookup is answered from the local cache when
possible, otherwise forwarded upstream. Each lookup is logged to CSV
(timestamp, client, domain, type, hit/miss, latency, TTL), which is also a
trace the simulator can replay.

With ``--predict`` it learns follow-up probabilities online from the traffic
it sees (same transition model as the simulator) and prefetches names whose
probability is above the threshold, respecting their TTL.

    python -m dnscache.collect.forwarder --port 5300 --log data/live/queries.csv --predict
    nslookup -port=5300 example.com 127.0.0.1           (Windows)
    dig @127.0.0.1 -p 5300 example.com                  (Linux/macOS)
"""
from __future__ import annotations

import argparse
import csv
import socket
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from . import dnsmsg


class OnlineFollowups:
    """Transition counts updated as lookups arrive (per client, 30 s window)."""

    def __init__(self, window_s=30.0, min_support=3, threshold=0.6):
        self.window_s, self.min_support, self.threshold = window_s, min_support, threshold
        self.count = defaultdict(int)
        self.pair = defaultdict(lambda: defaultdict(int))
        self.recent = defaultdict(deque)
        self.lock = threading.Lock()

    def observe(self, client: str, key: str, now: float) -> None:
        with self.lock:
            dq = self.recent[client]
            while dq and now - dq[0][0] > self.window_s:
                dq.popleft()
            for _, a, seen in dq:
                if a != key and key not in seen:
                    seen.add(key)
                    self.pair[a][key] += 1
            self.count[key] += 1
            dq.append((now, key, set()))

    def predict(self, key: str) -> list[tuple[str, float]]:
        with self.lock:
            n = self.count.get(key, 0)
            if n < self.min_support:
                return []
            return sorted(((b, c / n) for b, c in self.pair[key].items()
                           if c >= self.min_support and c / n >= self.threshold), key=lambda x: -x[1])[:5]


class CachingForwarder:
    def __init__(self, upstream=("8.8.8.8", 53), timeout=2.0, log_path=None, predict=False,
                 threshold=0.6, max_entries=10000):
        self.upstream, self.timeout = upstream, timeout
        self.cache: dict[str, tuple[bytes, float, int]] = {}    # key -> (response, stored_at, ttl)
        self.lock = threading.Lock()
        self.max_entries = max_entries
        self.followups = OnlineFollowups(threshold=threshold) if predict else None
        self.prefetched: set[str] = set()
        self.stats = defaultdict(int)
        self.log_path = Path(log_path) if log_path else None
        self._log_lock = threading.Lock()
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            new = not self.log_path.exists()
            self._log_f = self.log_path.open("a", newline="", encoding="utf-8")
            self._log = csv.writer(self._log_f)
            if new:
                self._log.writerow(["ts", "client", "qname", "qtype", "outcome", "latency_ms", "ttl", "prefetched"])

    # -------------------------------------------------------------- upstream
    def ask_upstream(self, pkt: bytes) -> bytes:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(self.timeout)
            s.sendto(pkt, self.upstream)
            data, _ = s.recvfrom(4096)
            return data

    def _store(self, key: str, resp: bytes) -> int:
        ttl = dnsmsg.min_ttl(resp)
        if ttl > 0:
            with self.lock:
                if len(self.cache) >= self.max_entries:
                    self.cache.pop(next(iter(self.cache)))
                self.cache[key] = (resp, time.time(), ttl)
        return ttl

    def prefetch(self, key: str) -> None:
        name, qtype = key.split("|")
        with self.lock:
            hit = self.cache.get(key)
            if hit and time.time() - hit[1] < hit[2] - 5:      # still valid: nothing to do
                return
        try:
            resp = self.ask_upstream(dnsmsg.build_query(name, qtype))
        except OSError:
            return
        if self._store(key, resp) > 0:
            self.prefetched.add(key)
            self.stats["prefetches"] += 1

    # -------------------------------------------------------------- serving
    def handle(self, pkt: bytes, client: str) -> bytes | None:
        t0 = time.time()
        try:
            qid, name, qtype = dnsmsg.parse_question(pkt)
        except (ValueError, IndexError, UnicodeDecodeError):
            return None
        key = f"{name}|{qtype}"
        with self.lock:
            hit = self.cache.get(key)
        outcome, was_pf = "miss", key in self.prefetched
        if hit and t0 - hit[1] < hit[2]:
            resp, stored, ttl = hit
            resp = dnsmsg.rewrite(resp, qid, int(t0 - stored))
            outcome = "hit"
            self.stats["hits"] += 1
            if was_pf:
                self.stats["prefetch_hits"] += 1
                self.prefetched.discard(key)
        else:
            try:
                resp = self.ask_upstream(pkt)
            except OSError:
                self.stats["errors"] += 1
                return None
            ttl = self._store(key, resp)
            self.stats["misses"] += 1
            self.prefetched.discard(key)
        ms = (time.time() - t0) * 1000
        self.stats["lookups"] += 1
        if self.log_path:
            with self._log_lock:
                self._log.writerow([f"{t0:.3f}", client, name, qtype, outcome, f"{ms:.2f}", ttl, int(was_pf)])
                self._log_f.flush()
        if self.followups:
            self.followups.observe(client, key, t0)
            for b, p in self.followups.predict(key):
                threading.Thread(target=self.prefetch, args=(b,), daemon=True).start()
        return resp

    def serve(self, host="127.0.0.1", port=5300) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((host, port))
        print(f"DNS forwarder on {host}:{port} -> {self.upstream[0]}:{self.upstream[1]}  (Ctrl+C to stop)")
        try:
            while True:
                pkt, addr = sock.recvfrom(4096)
                threading.Thread(target=self._reply, args=(sock, pkt, addr), daemon=True).start()
        except KeyboardInterrupt:
            pass
        finally:
            sock.close()
            print("stats:", dict(self.stats))

    def _reply(self, sock, pkt, addr):
        resp = self.handle(pkt, addr[0])
        if resp:
            sock.sendto(resp, addr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5300)
    ap.add_argument("--upstream", default="8.8.8.8")
    ap.add_argument("--log", default="data/live/queries.csv")
    ap.add_argument("--predict", action="store_true", help="learn follow-ups online and prefetch them")
    ap.add_argument("--threshold", type=float, default=0.6)
    a = ap.parse_args(argv)
    CachingForwarder((a.upstream, 53), log_path=a.log, predict=a.predict, threshold=a.threshold).serve(a.host, a.port)


if __name__ == "__main__":
    main()
