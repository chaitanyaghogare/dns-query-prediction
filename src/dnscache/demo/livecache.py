"""A real DNS cache that answers lookups, with optional prediction + prefetch.

The same class is used for both caches in the demo:

* ``LiveCache("normal")``: an ordinary TTL cache.
* ``LiveCache("ai", predictor=model)``: after each lookup it asks the
  follow-up model which domains usually come next. Every domain with
  probability >= threshold that is not already cached is fetched from the
  real upstream in the background and stored with its real TTL.

Time comes from the caller (``now`` in seconds), so a recorded session can be
replayed quickly while every upstream answer and latency is still real.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..data.schema import Query, cache_key


@dataclass
class Entry:
    addresses: list
    response: bytes | None
    ttl: int
    stored_at: float      # when the answer arrived
    source: str           # "lookup" or "prefetch"
    prob: float | None = None
    hits: int = 0

    def expires(self) -> float:
        return self.stored_at + self.ttl


@dataclass
class Result:
    t: float
    client: str
    domain: str
    outcome: str          # HIT / MISS / WAIT
    ms: float
    ttl_left: int
    addresses: list
    from_prefetch: bool = False
    prob: float | None = None
    response: bytes | None = None   # raw DNS answer (used by the DNS server mode)
    stored_at: float = 0.0


@dataclass
class Prediction:
    t: float
    client: str
    after: str
    domain: str
    prob: float
    reason: str
    action: str           # "prefetched" / "already cached"
    used: bool = False
    upstream_ms: float | None = None


@dataclass
class LiveCache:
    name: str
    predictor: object = None
    threshold: float = 0.7
    learn: bool = True
    clock: object = None        # real-time modes: callable giving "now"; answers count as stored when they arrive
    entries: dict = field(default_factory=dict)
    results: list = field(default_factory=list)
    predictions: list = field(default_factory=list)
    upstream_lookups: int = 0
    upstream_prefetches: int = 0

    def _valid(self, key, now):
        e = self.entries.get(key)
        return e if e is not None and now < e.expires() else None

    def lookup(self, now: float, client: str, domain: str, fetch, qtype: str = "A") -> Result:
        key = cache_key(domain, qtype)
        e = self._valid(key, now)
        if e is not None and now < e.stored_at:            # prefetch still on its way
            res = Result(now, client, domain, "WAIT", (e.stored_at - now) * 1000, e.ttl, e.addresses,
                         e.source == "prefetch", e.prob, e.response, e.stored_at)
            self._mark_used(e, key)
        elif e is not None:
            t0 = time.perf_counter()
            addrs = list(e.addresses)
            ms = (time.perf_counter() - t0) * 1000 + 0.05    # dictionary lookup + reply
            res = Result(now, client, domain, "HIT", ms, int(e.expires() - now), addrs,
                         e.source == "prefetch", e.prob, e.response, e.stored_at)
            self._mark_used(e, key)
        else:
            a = fetch(domain, qtype)
            self.upstream_lookups += 1
            arrived = self._arrival(now, a)
            if a.ttl > 0:
                self.entries[key] = Entry(a.addresses, a.response, a.ttl, arrived, "lookup")
            res = Result(now, client, domain, "MISS", a.ms, a.ttl, a.addresses, response=a.response,
                         stored_at=arrived)
        self.results.append(res)

        if self.predictor is not None:
            if self.learn:
                self.predictor.observe(Query(now, client, domain.lower(), qtype))
            for nxt, p in self.predictor.predict(key, self.threshold):
                self._prefetch(now, client, domain, nxt, p, fetch)
        return res

    def _arrival(self, now, a) -> float:
        return self.clock() if self.clock else now + a.ms / 1000

    def _mark_used(self, e: Entry, key: str) -> None:
        e.hits += 1
        if e.source == "prefetch" and e.hits == 1:
            for pr in reversed(self.predictions):
                if pr.domain == key.split("|")[0] and pr.action == "prefetched":
                    pr.used = True
                    break

    def _prefetch(self, now, client, after, key, p, fetch) -> None:
        dom, qtype = key.split("|")
        reason = self.predictor.explain(cache_key(after, qtype), key) if hasattr(self.predictor, "explain") else ""
        e = self.entries.get(key)
        if e is not None and now + 5 < e.expires():
            self.predictions.append(Prediction(now, client, after, dom, p, reason, "already cached"))
            return
        try:
            a = fetch(dom, qtype, prefetch=True)
        except OSError:
            return
        self.upstream_prefetches += 1
        if a.ttl > 0:
            self.entries[key] = Entry(a.addresses, a.response, a.ttl, self._arrival(now, a), "prefetch", p)
        self.predictions.append(Prediction(now, client, after, dom, p, reason, "prefetched", upstream_ms=a.ms))

    # ------------------------------------------------------------- summary
    def summary(self) -> dict:
        n = max(len(self.results), 1)
        lat = sorted(r.ms for r in self.results)
        pf = [p for p in self.predictions if p.action == "prefetched"]
        used = sum(p.used for p in pf)
        return {
            "lookups": len(self.results),
            "hits": sum(r.outcome == "HIT" for r in self.results),
            "waits": sum(r.outcome == "WAIT" for r in self.results),
            "misses": sum(r.outcome == "MISS" for r in self.results),
            "hit_rate": sum(r.outcome == "HIT" for r in self.results) / n,
            "mean_ms": sum(lat) / n if lat else 0.0,
            "p95_ms": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else 0.0,
            "total_wait_ms": sum(lat),
            "upstream_lookups": self.upstream_lookups,
            "upstream_prefetches": self.upstream_prefetches,
            "upstream_total": self.upstream_lookups + self.upstream_prefetches,
            "predictions": len(self.predictions),
            "prefetches": len(pf),
            "prefetches_used": used,
            "unnecessary_prefetches": len(pf) - used,
            "prefetch_precision": used / len(pf) if pf else None,
        }
