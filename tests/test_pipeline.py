"""End-to-end on a small synthetic trace: AI cache must beat the normal cache."""
from dnscache.predict.features import FutureIndex
from dnscache.predict.sequence import SequenceModel
from dnscache.sim import policies as P
from dnscache.sim.environment import Upstream
from dnscache.sim.simulator import Simulator
from dnscache.synth.generator import SynthConfig, generate


def test_followups_improve_hit_rate_on_synthetic_trace():
    qs, ttl = generate(SynthConfig(hours=8, n_clients=15))
    up, win = Upstream(ttl), (5 * 3600, 8 * 3600)
    seq = SequenceModel().fit([x for x in qs if x.ts < 5 * 3600])
    base = Simulator(qs, up, P.TTLOnly(), window=win).run()
    ai = Simulator(qs, up, P.FollowupOnly(seq, 0.7), window=win, future=FutureIndex(qs)).run()
    assert ai["hit_rate"] > base["hit_rate"] + 0.05
    assert ai["upstream_demand"] < base["upstream_demand"]
    assert ai["prefetch_precision"] > 0.5


def test_same_network_for_every_policy():
    up = Upstream({}, seed=3)
    assert up.latency_ms("a|A", 12.5) == up.latency_ms("a|A", 12.5)
