from dnscache.predict.features import FutureIndex
from dnscache.sim import policies as P
from dnscache.sim.cache import Cache, Entry
from dnscache.sim.simulator import Simulator
from helpers import FixedUpstream, q

TTL = {"a.com|A": 60, "b.com|A": 60, "c.com|A": 60}


def run(queries, policy=None, capacity=None, ms=100.0):
    sim = Simulator(queries, FixedUpstream(TTL, ms), policy or P.TTLOnly(), capacity=capacity,
                    future=FutureIndex(queries))
    outcomes = [sim.lookup(x)[0] for x in queries]
    return sim, outcomes


def test_lru_eviction_order():
    c = Cache(2)
    for k in "abc":
        ev = c.put(Entry(k, 60, 0, 0, 60))
    assert [e.key for e in ev] == ["a"] and "a" not in c


def test_hit_within_ttl_and_miss_after():
    sim, out = run([q(0, "a.com"), q(10, "a.com"), q(70, "a.com")])
    assert out == ["miss_first", "hit", "miss_expired"]
    assert sim.m.upstream_demand == 2


def test_inflight_coalescing():
    sim, out = run([q(0, "a.com"), q(0.05, "a.com")], ms=100)
    assert out == ["miss_first", "inflight"]
    assert sim.m.upstream_demand == 1
    assert abs(sim.m.latencies[1] - 50) < 1e-6


def test_eviction_miss_type():
    _, out = run([q(0, "a.com"), q(1, "b.com"), q(2, "a.com")], capacity=1)
    assert out[-1] == "miss_evicted"


def test_refresh_if_used_keeps_entry_alive():
    qs = [q(0, "a.com"), q(10, "a.com"), q(80, "a.com")]
    _, base = run(qs)
    sim, riu = run(qs, P.RefreshIfUsed())
    assert base[-1] == "miss_expired" and riu[-1] == "hit"
    assert sim.m.prefetch_issued["refresh"] == 1 and sim.m.prefetch_useful["refresh"] == 1


def test_unused_entry_is_not_refreshed_by_rule():
    sim, out = run([q(0, "a.com"), q(80, "a.com")], P.RefreshIfUsed())
    assert out[-1] == "miss_expired"
    assert sum(sim.m.prefetch_issued.values()) == 0


def test_oracle_refreshes_only_when_needed():
    qs = [q(0, "a.com"), q(0, "b.com"), q(80, "a.com")]
    sim, out = run(qs, P.OracleRefresh())
    assert out[-1] == "hit"
    assert sim.m.prefetch_issued["refresh"] == 1          # b.com was left to expire


def test_unbound_prefetch_on_late_hit():
    sim, out = run([q(0, "a.com"), q(57, "a.com"), q(90, "a.com")], P.UnboundPrefetch())
    assert out == ["miss_first", "hit", "hit"]


def test_ml_policy_uses_predictor_and_logs():
    qs = [q(0, "a.com"), q(80, "a.com")]
    pol = P.ExpiryRefreshPolicy(lambda x: 0.9, 0.5)
    sim = Simulator(qs, FixedUpstream(TTL), pol, record=(0, 1000), future=FutureIndex(qs))
    out = [sim.lookup(x)[0] for x in qs]
    assert out[-1] == "hit"
    assert sim.decision_log and sim.decision_log[0]["refresh"] is True


def test_followup_prefetch_turns_first_miss_into_hit():
    class Seq:
        def predict(self, key, thr):
            return [("b.com|A", 0.9)] if key == "a.com|A" else []
    qs = [q(0, "a.com"), q(1, "b.com")]
    _, base = run(qs)
    sim, out = run(qs, P.FollowupOnly(Seq(), 0.7))
    assert base[1] == "miss_first" and out[1] == "hit"
    assert sim.m.prefetch_useful["followup"] == 1


def test_window_only_counts_inside():
    qs = [q(0, "a.com"), q(10, "a.com"), q(20, "a.com")]
    sim = Simulator(qs, FixedUpstream(TTL), P.TTLOnly(), window=(5, 15))
    s = sim.run()
    assert s["lookups"] == 1 and s["hit_rate"] == 1.0


def test_summary_percentiles():
    qs = [q(i * 100, "a.com") for i in range(10)]
    s = Simulator(qs, FixedUpstream(TTL, 100), P.TTLOnly()).run()
    assert s["miss_rate"] == 1.0 and abs(s["p95_ms"] - 100) < 1e-9
