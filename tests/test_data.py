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


def _pcap(path, packets, pcapng=False):
    """Write a tiny capture of DNS-over-UDP/IPv4/Ethernet packets: (ts, src, dst, sport, dport, dns)."""
    import struct

    def frame(src, dst, sport, dport, dns):
        udp = struct.pack("!HHHH", sport, dport, 8 + len(dns), 0) + dns
        ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 64, 17, 0,
                         bytes(map(int, src.split("."))), bytes(map(int, dst.split("."))))
        return b"\0" * 12 + b"\x08\x00" + ip + udp

    out = bytearray()
    if pcapng:
        out += struct.pack("<II I HHq I", 0x0A0D0D0A, 28, 0x1A2B3C4D, 1, 0, -1, 28)
        out += struct.pack("<IIHHI I", 1, 20, 1, 0, 65535, 20)
        for ts, *rest in packets:
            f = frame(*rest)
            pad = (4 - len(f) % 4) % 4
            us = int(ts * 1e6)
            blen = 32 + len(f) + pad
            out += struct.pack("<IIIIIII", 6, blen, 0, us >> 32, us & 0xFFFFFFFF, len(f), len(f)) + f + b"\0" * pad
            out += struct.pack("<I", blen)
    else:
        out += struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
        for ts, *rest in packets:
            f = frame(*rest)
            out += struct.pack("<IIII", int(ts), int(round(ts % 1 * 1e6)), len(f), len(f)) + f
    path.write_bytes(bytes(out))


def _response(name, ttl):
    import struct
    from dnscache.collect.dnsmsg import build_query
    q = bytearray(build_query(name, qid=7))
    struct.pack_into("!HH", q, 2, 0x8180, 1)
    q[6:8] = b"\x00\x01"
    return bytes(q) + b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, ttl, 4) + b"\x01\x02\x03\x04"


def test_pcap_adapter_reads_queries_and_ttls(tmp_path):
    from dnscache.collect.dnsmsg import build_query
    from dnscache.data.adapters import pcap
    t0 = 1760918400.0                                   # a Monday 00:00 UTC
    pk = [(t0 + 1.0, "10.0.0.1", "10.0.0.53", 5000, 53, build_query("Example.com", "A")),
          (t0 + 1.02, "10.0.0.53", "10.0.0.1", 53, 5000, _response("example.com", 120)),
          (t0 + 2.5, "10.0.0.2", "10.0.0.53", 5001, 53, build_query("ipv6.example.com", "AAAA")),
          (t0 + 3.0, "10.0.0.2", "10.0.0.53", 5001, 80, b"not dns")]
    for ng in (False, True):
        p = tmp_path / ("x.pcapng" if ng else "x.pcap")
        _pcap(p, pk, ng)
        qs, ttl = pcap.load(p)
        assert [(q.client, q.qname, q.qtype) for q in qs] == [
            ("10.0.0.1", "example.com", "A"), ("10.0.0.2", "ipv6.example.com", "AAAA")]
        assert abs(qs[0].ts - 1.0) < 1e-3 and abs(qs[1].ts - 2.5) < 1e-3
        assert ttl == {"example.com|A": 120}
    assert [q.client for q in pcap.load(tmp_path / "x.pcap", exclude=["10.0.0.1"])[0]] == ["10.0.0.2"]
    # IST shift moves the trace to 05:30 local time
    assert abs(pcap.load(tmp_path / "x.pcap", utc_offset_h=5.5)[0][0].ts - (5.5 * 3600 + 1)) < 1e-3


def test_pcap_client_sample_is_deterministic():
    from dnscache.data.adapters import pcap
    ips = [f"10.0.{i // 256}.{i % 256}" for i in range(2000)]
    kept = [ip for ip in ips if pcap.sampled(ip, 0.1)]
    assert 150 < len(kept) < 250 and kept == [ip for ip in ips if pcap.sampled(ip, 0.1)]
