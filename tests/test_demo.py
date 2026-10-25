"""The live demo, end to end, against a local fake DNS server standing in for the internet."""
import json
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

from dnscache.collect import dnsmsg
from dnscache.demo.__main__ import main as demo_main
from dnscache.demo.livecache import LiveCache
from dnscache.demo.upstream import Answer, RealUpstream
from dnscache.predict.sequence import SequenceModel
from dnscache.data.schema import Query
from test_forwarder import make_response

ROOT = Path(__file__).resolve().parents[1]


class SlowFakeDNS:
    """Answers every A query with 1.2.3.4, TTL 300, after `delay` seconds."""

    def __init__(self, delay=0.005):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.port = self.sock.getsockname()[1]
        self.delay, self.count = delay, 0
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                pkt, a = self.sock.recvfrom(4096)
            except OSError:
                return
            self.count += 1
            time.sleep(self.delay)
            self.sock.sendto(make_response(pkt), a)


def fake_fetch(ttl=300, ms=40.0):
    calls = []

    def fetch(name, qtype="A", prefetch=False):
        calls.append((name, prefetch))
        return Answer(None, ["1.2.3.4"], ttl, ms)
    return fetch, calls


def test_livecache_hit_miss_and_ttl():
    c = LiveCache("normal")
    fetch, calls = fake_fetch(ttl=60)
    assert c.lookup(0, "c", "a.com", fetch).outcome == "MISS"
    assert c.lookup(0.01, "c", "a.com", fetch).outcome == "WAIT"       # answer still on its way
    assert c.lookup(10, "c", "a.com", fetch).outcome == "HIT"
    assert c.lookup(61, "c", "a.com", fetch).outcome == "MISS"         # TTL respected
    assert len(calls) == 2


def test_ai_cache_prefetches_predicted_domain():
    m = SequenceModel(min_support=3).fit([Query(i * 100 + d, "c", n) for i in range(5) for d, n in ((0, "a.com"), (1, "b.com"))])
    ai = LiveCache("ai", predictor=m, threshold=0.7)
    fetch, calls = fake_fetch()
    ai.lookup(1000, "c", "a.com", fetch)
    assert ("b.com", True) in calls
    r = ai.lookup(1001, "c", "b.com", fetch)
    assert r.outcome == "HIT" and r.from_prefetch
    s = ai.summary()
    assert s["prefetches_used"] == 1 and s["upstream_prefetches"] == 1


def test_real_upstream_client_against_fake_server():
    srv = SlowFakeDNS(0.02)
    a = RealUpstream("127.0.0.1", srv.port).fetch("x.com")
    assert a.addresses == ["1.2.3.4"] and a.ttl == 300 and a.ms >= 15


def test_demo_run_end_to_end(tmp_path, capsys):
    srv = SlowFakeDNS()
    demo_main(["run", "--upstream", f"127.0.0.1:{srv.port}", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert "AI:" in out and "predicts" in out and "report.html" in out
    s = json.loads((tmp_path / "summary.json").read_text())
    assert s["ai"]["hit_rate"] > s["normal"]["hit_rate"]
    assert s["ai"]["upstream_lookups"] < s["normal"]["upstream_lookups"]
    for f in ("report.html", "lookups.csv", "predictions.csv"):
        assert (tmp_path / f).stat().st_size > 500


def test_demo_domain_list_input(tmp_path, capsys):
    lst = tmp_path / "list.txt"
    lst.write_text("# my lookups\nwww.youtube.com\n0.3 i.ytimg.com\n5 www.youtube.com\n")
    demo_main(["run", "--offline", "--domains", str(lst), "--out", str(tmp_path / "o")])
    lines = (tmp_path / "o" / "lookups.csv").read_text().splitlines()
    assert len(lines) == 4 and ",HIT," in lines[2]           # i.ytimg.com was prefetched


def test_demo_serve_answers_nslookup_style_queries(tmp_path):
    srv = SlowFakeDNS()
    port = 5399
    p = subprocess.Popen([sys.executable, str(ROOT / "demo.py"), "serve", "--upstream", f"127.0.0.1:{srv.port}",
                          "--port", str(port), "--out", str(tmp_path)], stdout=subprocess.PIPE, text=True)
    try:
        time.sleep(1.5)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3)
            s.sendto(dnsmsg.build_query("www.youtube.com", qid=77), ("127.0.0.1", port))
            r1, _ = s.recvfrom(4096)
            time.sleep(0.2)
            s.sendto(dnsmsg.build_query("i.ytimg.com", qid=78), ("127.0.0.1", port))
            r2, _ = s.recvfrom(4096)
        assert struct.unpack("!H", r1[:2])[0] == 77 and dnsmsg.parse_answers(r2)[0][3] == "1.2.3.4"
    finally:
        p.terminate()
        p.wait(5)
