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


def test_fractional_windows_cover_the_whole_trace():
    from dnscache.data.schema import Query
    from dnscache.run import resolve_windows
    cfg = {"windows_frac": {"train": [0, 0.5], "test": [0.5, 1]}, "replay_start_frac": 0.75}
    qs = [Query(100 * 3600 + 1.2, "c", "a.com"), Query(110 * 3600 + 0.7, "c", "b.com")]
    resolve_windows(cfg, qs)
    assert cfg["windows_h"] == {"train": [100.0, 105.0], "test": [105.0, 110.01]}
    assert cfg["replay_start_h"] == 107.5
