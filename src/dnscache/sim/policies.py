"""Cache policies. All share the same interface so the simulator never changes:

* ``on_expiry(sim, entry, now)`` -> refresh this entry before it expires?
* ``on_hit(sim, entry, now)``    -> refresh now, on this hit?
* ``followups(sim, key, now)``   -> names to prefetch after a lookup of ``key``

``ExpiryRefreshPolicy`` is the slot where the ML predictor plugs in.
"""
from __future__ import annotations


class Policy:
    name = "base"
    label = "Base"

    def on_expiry(self, sim, entry, now) -> bool:
        return False

    def on_hit(self, sim, entry, now) -> bool:
        return False

    def followups(self, sim, key, now) -> list:
        return []


class TTLOnly(Policy):
    """Conventional cache: keep until TTL runs out, then forget."""
    name, label = "ttl_only", "Normal TTL cache"


class UnboundPrefetch(Policy):
    """Unbound's `prefetch: yes`: a hit in the last 10% of the TTL triggers a refresh."""
    name, label = "unbound_prefetch", "Unbound-style prefetch"

    def on_hit(self, sim, entry, now) -> bool:
        return entry.expires_at - now < 0.1 * entry.ttl


class RefreshIfUsed(Policy):
    """Simple heuristic: refresh an expiring entry if it was used during this lifetime."""
    name, label = "refresh_if_used", "Refresh-if-used rule"

    def on_expiry(self, sim, entry, now) -> bool:
        return entry.hits > 0


class OracleRefresh(Policy):
    """Upper bound: knows the future and refreshes exactly the entries that will be used."""
    name, label = "oracle_refresh", "Oracle (knows the future)"

    def on_expiry(self, sim, entry, now) -> bool:
        return sim.future.queried_between(entry.key, entry.expires_at, entry.expires_at + entry.ttl)


class ExpiryRefreshPolicy(Policy):
    """ML policy: refresh when predictor(features) >= threshold.

    ``predictor`` is any callable features -> probability.
    """
    name, label = "ml_refresh", "ML refresh (decision tree)"

    def __init__(self, predictor, threshold: float = 0.5, followup_model=None, followup_threshold: float = 0.7,
                 name: str | None = None, label: str | None = None):
        self.predictor = predictor
        self.threshold = threshold
        self.followup_model = followup_model
        self.followup_threshold = followup_threshold
        if name:
            self.name = name
        if label:
            self.label = label

    def on_expiry(self, sim, entry, now) -> bool:
        x = sim.stats.features(entry.key, now, entry)
        p = self.predictor(x)
        refresh = p >= self.threshold
        if sim.recording(now):
            sim.decision_log.append({"t": round(now, 2), "name": entry.key.split("|")[0], "p": round(p, 3),
                                     "refresh": refresh, "hits": entry.hits, "ttl": entry.ttl})
        return refresh

    def followups(self, sim, key, now) -> list:
        if self.followup_model is None:
            return []
        return self.followup_model.predict(key, self.followup_threshold)


class FollowupOnly(Policy):
    """Plain TTL cache + transition-probability prefetching only (ablation)."""
    name, label = "followup_only", "TTL cache + follow-up prefetch"

    def __init__(self, model, threshold: float = 0.7):
        self.model, self.threshold = model, threshold

    def followups(self, sim, key, now) -> list:
        return self.model.predict(key, self.threshold)


class Collector(Policy):
    """Wraps a policy and records (features, label) at every expiry decision.

    Label = 1 if the name is asked for again during the lifetime a refresh
    would have bought, i.e. in [expiry, expiry + TTL).
    """

    def __init__(self, inner: Policy):
        self.inner = inner
        self.name = "collect:" + inner.name
        self.rows: list[tuple[float, str, list[float], int]] = []

    def on_expiry(self, sim, entry, now) -> bool:
        x = sim.stats.features(entry.key, now, entry)
        y = int(sim.future.queried_between(entry.key, entry.expires_at, entry.expires_at + entry.ttl))
        self.rows.append((now, entry.key, x, y))
        return self.inner.on_expiry(sim, entry, now)

    def on_hit(self, sim, entry, now) -> bool:
        return self.inner.on_hit(sim, entry, now)

    def followups(self, sim, key, now) -> list:
        return self.inner.followups(sim, key, now)
