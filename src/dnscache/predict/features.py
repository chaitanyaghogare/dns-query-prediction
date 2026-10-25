"""Features available at decision time only (no peeking at the future).

A ``KeyStats`` object per cache key is updated online as lookups arrive.
At a decision point the simulator calls ``features()``; the result is the
model input. ``FutureIndex`` is the only thing that knows the future and it
is used for training labels and the oracle, never as a model input.
"""
from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict, deque

FEATURES = [
    "ttl",               # TTL of the record (s)
    "hits_this_life",    # lookups answered by the entry that is about to expire
    "since_last_q",      # seconds since the name was last asked for
    "since_last_over_ttl",
    "q_1m", "q_5m", "q_30m",   # lookups in the last 1 / 5 / 30 minutes
    "clients_30m",       # distinct clients in the last 30 minutes
    "total_q",           # lookups so far
    "mean_gap",          # average seconds between lookups so far
    "hour",              # hour of day
    "was_refresh",       # current lifetime came from a refresh/prefetch
    "unused_refreshes",  # refreshed lifetimes in a row that nobody used
]


class KeyStats:
    __slots__ = ("recent", "total", "first", "last")

    def __init__(self):
        self.recent: deque = deque()   # (ts, client) within the last 30 minutes
        self.total = 0
        self.first = None
        self.last = None

    def add(self, ts: float, client: str) -> None:
        self.recent.append((ts, client))
        self.total += 1
        if self.first is None:
            self.first = ts
        self.last = ts
        cutoff = ts - 1800
        while self.recent and self.recent[0][0] < cutoff:
            self.recent.popleft()


class StatsTable:
    def __init__(self):
        self.by_key: dict[str, KeyStats] = defaultdict(KeyStats)

    def observe(self, key: str, ts: float, client: str) -> None:
        self.by_key[key].add(ts, client)

    def features(self, key: str, now: float, entry) -> list[float]:
        s = self.by_key.get(key) or KeyStats()
        q1 = q5 = q30 = 0
        clients = set()
        for ts, c in s.recent:
            age = now - ts
            if age <= 1800:
                q30 += 1
                clients.add(c)
                if age <= 300:
                    q5 += 1
                    if age <= 60:
                        q1 += 1
        since = now - s.last if s.last is not None else 1e6
        gap = (s.last - s.first) / (s.total - 1) if s.total > 1 else 1e6
        ttl = entry.ttl
        return [ttl, entry.hits, since, since / ttl, q1, q5, q30, len(clients), s.total,
                min(gap, 1e6), int(now // 3600) % 24, int(entry.prefetched), entry.unused_refreshes]


class FutureIndex:
    """Sorted lookup times per key, for labels and the oracle."""

    def __init__(self, queries):
        self.times: dict[str, list[float]] = defaultdict(list)
        for q in queries:
            self.times[q.key].append(q.ts)

    def queried_between(self, key: str, start: float, end: float) -> bool:
        ts = self.times.get(key)
        if not ts:
            return False
        i = bisect_left(ts, start)
        return i < len(ts) and ts[i] < end
