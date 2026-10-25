"""Upstream environment: what the upstream resolver returns on a miss or prefetch.

* TTL per cache key (from the trace catalogue, or a default).
* Latency of an upstream fetch. It is a deterministic function of
  (seed, key, time), so every policy sees exactly the same network: a
  difference between policies is never just random noise.
"""
from __future__ import annotations

import math
import random
import zlib

HIT_LATENCY_MS = 0.5


class Upstream:
    def __init__(self, ttl_by_key: dict[str, int] | None = None, default_ttl: int = 300, seed: int = 1,
                 median_ms: float = 35.0, slow_prob: float = 0.06):
        self.ttl_by_key = ttl_by_key or {}
        self.default_ttl = default_ttl
        self.seed = seed
        self.median_ms = median_ms
        self.slow_prob = slow_prob
        self._base: dict[str, float] = {}

    def ttl(self, key: str) -> int:
        return self.ttl_by_key.get(key, self.default_ttl)

    def _rng(self, *parts) -> random.Random:
        return random.Random(zlib.crc32("|".join(map(str, (self.seed, *parts))).encode()))

    def base_ms(self, key: str) -> float:
        """Per-domain distance to its authoritative servers."""
        b = self._base.get(key)
        if b is None:
            b = self._base[key] = self.median_ms * math.exp(self._rng("base", key).gauss(0, 0.6))
        return b

    def latency_ms(self, key: str, now: float) -> float:
        r = self._rng(key, f"{now:.3f}")
        ms = self.base_ms(key) * math.exp(r.gauss(0, 0.3))
        if r.random() < self.slow_prob:          # retries / slow authoritative chain
            ms += r.uniform(200, 1500)
        return ms
