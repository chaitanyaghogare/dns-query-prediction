from dnscache.data.schema import Query
from dnscache.sim.environment import Upstream


class FixedUpstream(Upstream):
    """Every fetch takes exactly `ms` milliseconds."""

    def __init__(self, ttl_by_key, ms=100.0, default_ttl=60):
        super().__init__(ttl_by_key, default_ttl=default_ttl)
        self.ms = ms

    def latency_ms(self, key, now):
        return self.ms


def q(ts, name, client="c1"):
    return Query(ts, client, name)
