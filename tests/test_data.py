from dnscache.data import io
from dnscache.data.adapters import canonical, tshark
from dnscache.data.assess import assess
from dnscache.data.schema import Query, cache_key
from dnscache.synth.generator import SynthConfig, generate


def test_cache_key_normalises():
    assert cache_key("WWW.Example.COM.", "a") == "www.example.com|A"


def test_trace_roundtrip(tmp_path):
    qs = [Query(0.5, "c1", "a.com"), Query(1.25, "c2", "b.com", "AAAA")]
    io.write_trace(tmp_path / "t.csv", qs, {"source": "test"})
    assert io.read_trace(tmp_path / "t.csv") == qs
    assert io.read_manifest(tmp_path / "t.csv")["rows"] == 2


def test_generator_is_deterministic():
    cfg = SynthConfig(hours=2, n_clients=5)
    a, ta = generate(cfg)
    b, tb = generate(cfg)
    assert a == b and ta == tb and len(a) > 100
    assert all(x.ts <= y.ts for x, y in zip(a, a[1:]))


def test_generator_seed_changes_trace():
    assert generate(SynthConfig(hours=2, n_clients=5, seed=1))[0] != generate(SynthConfig(hours=2, n_clients=5, seed=2))[0]


def test_assess_flags_short_trace():
    qs, _ = generate(SynthConfig(hours=2, n_clients=5))
    assert "shorter than 6 h" in assess(qs)["problems"][0]


def test_tshark_adapter(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("frame.time_epoch,ip.src,dns.qry.name,dns.qry.type\n"
                 "1760918400.0,10.0.0.1,Example.com,1\n1760918401.5,10.0.0.2,ipv6.example.com,28\n")
    qs = tshark.load(p)
    assert [x.qtype for x in qs] == ["A", "AAAA"] and qs[0].qname == "example.com"
    assert qs[1].ts - qs[0].ts == 1.5 and qs[0].ts < 7 * 86400


def test_canonical_adapter_keeps_relative_times(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text("ts,client,qname\n10,c,a.com\n5,c,b.com\n")
    assert [x.ts for x in canonical.load(p)] == [5.0, 10.0]
