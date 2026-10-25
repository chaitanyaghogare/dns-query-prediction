"""Discrete-event trace replay of a DNS cache.

Fast path (every lookup, no ML): hit -> answer from cache; miss -> ask upstream,
insert with the upstream TTL. Lookups that arrive while the answer is still on
its way wait for it ("in flight") instead of sending a second request.

Slow path (background, where the policy/ML lives): shortly before an entry
expires (``decision_lead`` seconds) the policy decides whether to refresh it.
Policies may also refresh on a hit (Unbound style) or prefetch names that are
predicted to follow the current lookup.

Only lookups inside ``window`` are counted, but the whole trace is replayed so
the cache is warm when the window starts.
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field

from ..predict.features import FutureIndex, StatsTable
from .cache import Cache, Entry
from .environment import HIT_LATENCY_MS, Upstream

HIT, INFLIGHT, MISS_FIRST, MISS_EXPIRED, MISS_EVICTED = "hit", "inflight", "miss_first", "miss_expired", "miss_evicted"


@dataclass
class Metrics:
    lookups: int = 0
    outcomes: dict = field(default_factory=lambda: defaultdict(int))
    latencies: list = field(default_factory=list)
    upstream_demand: int = 0
    prefetch_issued: dict = field(default_factory=lambda: defaultdict(int))   # by source
    prefetch_useful: dict = field(default_factory=lambda: defaultdict(int))
    occupancy: list = field(default_factory=list)
    hourly: dict = field(default_factory=lambda: defaultdict(lambda: [0, 0]))  # hour -> [hits, lookups]

    def summary(self) -> dict:
        n = max(self.lookups, 1)
        lat = sorted(self.latencies)

        def pct(p):
            return lat[min(len(lat) - 1, int(math.ceil(p / 100 * len(lat))) - 1)] if lat else 0.0

        issued = sum(self.prefetch_issued.values())
        useful = sum(self.prefetch_useful.values())
        misses = sum(v for k, v in self.outcomes.items() if k.startswith("miss"))
        return {
            "lookups": self.lookups,
            "hit_rate": self.outcomes[HIT] / n,
            "inflight_rate": self.outcomes[INFLIGHT] / n,
            "miss_rate": misses / n,
            "miss_first": self.outcomes[MISS_FIRST], "miss_expired": self.outcomes[MISS_EXPIRED],
            "miss_evicted": self.outcomes[MISS_EVICTED],
            "mean_ms": sum(lat) / len(lat) if lat else 0.0,
            "p50_ms": pct(50), "p95_ms": pct(95), "p99_ms": pct(99),
            "upstream_demand": self.upstream_demand,
            "upstream_prefetch": issued,
            "upstream_total": self.upstream_demand + issued,
            "upstream_per_lookup": (self.upstream_demand + issued) / n,
            "prefetch_issued": dict(self.prefetch_issued),
            "prefetch_useful": dict(self.prefetch_useful),
            "prefetch_precision": useful / issued if issued else None,
            "wasted_prefetches": issued - useful,
            "mean_occupancy": sum(self.occupancy) / len(self.occupancy) if self.occupancy else 0,
            "hourly_hit_rate": {h: (v[0] / v[1] if v[1] else None) for h, v in sorted(self.hourly.items())},
        }


class Simulator:
    def __init__(self, queries, upstream: Upstream, policy, capacity: int | None = None,
                 window: tuple[float, float] = (0.0, float("inf")), decision_lead: float = 2.0,
                 record: tuple[float, float] | None = None, future: FutureIndex | None = None):
        self.queries = queries
        self.up = upstream
        self.policy = policy
        self.cache = Cache(capacity)
        self.window = window
        self.lead = decision_lead
        self.record = record
        self.future = future
        self.stats = StatsTable()
        self.m = Metrics()
        self.events: list = []
        self._seq = 0
        self._gen = 0
        self.seen: set[str] = set()
        self.evicted: set[str] = set()
        self.lookup_log: list[dict] = []      # filled only inside `record`
        self.decision_log: list[dict] = []
        self.prefetch_log: list[dict] = []
        self.now = 0.0

    # ---------------------------------------------------------------- helpers
    def in_window(self, t: float) -> bool:
        return self.window[0] <= t < self.window[1]

    def recording(self, t: float) -> bool:
        return self.record is not None and self.record[0] <= t < self.record[1]

    def _push(self, t: float, kind: str, key: str, gen: int) -> None:
        self._seq += 1
        heapq.heappush(self.events, (t, self._seq, kind, key, gen))

    def _new_gen(self) -> int:
        self._gen += 1
        return self._gen

    def _schedule_decision(self, e: Entry) -> None:
        self._push(max(e.ready_at, e.expires_at - self.lead), "decide", e.key, e.gen)

    def _insert(self, key: str, t: float, prefetched: bool) -> tuple[Entry, float]:
        lat = self.up.latency_ms(key, t)
        ttl = self.up.ttl(key)
        ready = t + lat / 1000
        e = Entry(key, ttl, t, ready, ready + ttl, prefetched=prefetched, gen=self._new_gen())
        for old in self.cache.put(e):
            self.evicted.add(old.key)
        self.evicted.discard(key)
        self._schedule_decision(e)
        return e, lat

    def refresh(self, e: Entry, t: float, source: str, p: float | None = None) -> None:
        """Send a background upstream request that renews ``e`` when it lands."""
        if e.pending_ready is not None:
            return
        lat = self.up.latency_ms(e.key, t)
        e.pending_fetched, e.pending_ready = t, t + lat / 1000
        self._push(e.pending_ready, "land:" + source, e.key, e.gen)
        if self.in_window(t):
            self.m.prefetch_issued[source] += 1
        if self.recording(t):
            self.prefetch_log.append({"t": round(t, 2), "name": e.key.split("|")[0], "source": source,
                                      "p": None if p is None else round(p, 3)})

    def prefetch_new(self, key: str, t: float, source: str, p: float | None = None) -> None:
        """Prefetch a name that is predicted to be asked for soon."""
        e = self.cache.get(key)
        if e is not None:
            if e.pending_ready is not None or e.expires_at > t + 30:
                return
            if t < e.ready_at:
                return
            self.refresh(e, t, source, p)
            return
        e, _ = self._insert(key, t, prefetched=True)
        e.source = source
        if self.in_window(t):
            self.m.prefetch_issued[source] += 1
        if self.recording(t):
            self.prefetch_log.append({"t": round(t, 2), "name": key.split("|")[0], "source": source,
                                      "p": None if p is None else round(p, 3)})

    def _use(self, e: Entry, t: float) -> None:
        e.hits += 1
        if e.prefetched and e.hits == 1 and self.in_window(e.fetched_at):
            self.m.prefetch_useful[e.source] += 1
        self.cache.touch(e.key)

    # ---------------------------------------------------------------- events
    def _handle(self, t: float, kind: str, key: str, gen: int) -> None:
        e = self.cache.get(key)
        if e is None or e.gen != gen:
            return
        if kind == "decide":
            if e.pending_ready is not None:
                return
            if self.policy.on_expiry(self, e, t):
                self.refresh(e, t, "refresh")
            else:
                self._push(e.expires_at, "expire", key, gen)
        elif kind == "expire":
            if e.pending_ready is None:
                self.cache.remove(key)
        elif kind.startswith("land:"):
            e.unused_refreshes = (e.unused_refreshes + 1) if (e.prefetched and e.hits == 0) else 0
            e.fetched_at, e.ready_at = e.pending_fetched, e.pending_ready
            e.expires_at = e.ready_at + e.ttl
            e.pending_ready = e.pending_fetched = None
            e.prefetched, e.gen, e.source = True, self._new_gen(), kind[5:]
            e.hits, e.pending_waiters = e.pending_waiters, 0
            if e.hits and self.in_window(e.fetched_at):
                self.m.prefetch_useful[e.source] += 1
            self._schedule_decision(e)

    def _drain(self, until: float) -> None:
        ev = self.events
        while ev and ev[0][0] <= until:
            t, _, kind, key, gen = heapq.heappop(ev)
            self.now = t
            self._handle(t, kind, key, gen)

    # ---------------------------------------------------------------- lookups
    def lookup(self, q) -> tuple[str, float]:
        t, key = q.ts, q.key
        self._drain(t)
        self.now = t
        e = self.cache.get(key)
        if e is not None and t < e.ready_at:
            outcome, lat = INFLIGHT, (e.ready_at - t) * 1000
            self._use(e, t)
        elif e is not None and t < e.expires_at:
            outcome, lat = HIT, HIT_LATENCY_MS
            self._use(e, t)
            if self.policy.on_hit(self, e, t):
                self.refresh(e, t, "refresh")
        elif e is not None and e.pending_ready is not None:
            outcome, lat = INFLIGHT, (e.pending_ready - t) * 1000
            e.pending_waiters += 1
        else:
            outcome = MISS_EVICTED if key in self.evicted else (MISS_EXPIRED if key in self.seen else MISS_FIRST)
            e, lat = self._insert(key, t, prefetched=False)
            if self.in_window(t):
                self.m.upstream_demand += 1
        self.seen.add(key)
        self.stats.observe(key, t, q.client)

        for name, p in self.policy.followups(self, key, t):
            self.prefetch_new(name, t, "followup", p)

        if self.in_window(t):
            m = self.m
            m.lookups += 1
            m.outcomes[outcome] += 1
            m.latencies.append(lat)
            hb = m.hourly[int(t // 3600)]
            hb[1] += 1
            if outcome == HIT:
                hb[0] += 1
            if m.lookups % 200 == 0:
                m.occupancy.append(len(self.cache))
        if self.recording(t):
            self.lookup_log.append({"t": round(t, 2), "client": q.client, "name": q.qname,
                                    "outcome": outcome, "ms": round(lat, 1)})
        return outcome, lat

    def run(self) -> dict:
        for q in self.queries:
            if q.ts >= self.window[1]:
                break
            self.lookup(q)
        return self.m.summary()
