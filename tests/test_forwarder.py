import socket
import struct
import threading

from dnscache.collect import dnsmsg
from dnscache.collect.forwarder import CachingForwarder, OnlineFollowups


def make_response(query: bytes, ip=(1, 2, 3, 4), ttl=300) -> bytes:
    qid = struct.unpack("!H", query[:2])[0]
    question = query[12:]
    hdr = struct.pack("!HHHHHH", qid, 0x8180, 1, 1, 0, 0)
    ans = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, ttl, 4) + bytes(ip)
    return hdr + question + ans


class FakeUpstream:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.addr = self.sock.getsockname()
        self.count = 0
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        while True:
            try:
                pkt, a = self.sock.recvfrom(4096)
            except OSError:
                return
            self.count += 1
            self.sock.sendto(make_response(pkt), a)


def test_parse_and_build_roundtrip():
    q = dnsmsg.build_query("Www.Example.com", "AAAA", qid=42)
    assert dnsmsg.parse_question(q) == (42, "www.example.com", "AAAA")


def test_ttl_rewrite():
    r = make_response(dnsmsg.build_query("a.com", qid=1), ttl=300)
    assert dnsmsg.min_ttl(r) == 300
    r2 = dnsmsg.rewrite(r, 7, 100)
    assert struct.unpack("!H", r2[:2])[0] == 7
    assert dnsmsg.parse_answers(r2) == [("a.com", "A", 200, "1.2.3.4")]


def test_forwarder_caches_and_logs(tmp_path):
    up = FakeUpstream()
    fw = CachingForwarder(up.addr, log_path=tmp_path / "log.csv")
    r1 = fw.handle(dnsmsg.build_query("a.com", qid=1), "c1")
    r2 = fw.handle(dnsmsg.build_query("a.com", qid=2), "c1")
    assert up.count == 1 and struct.unpack("!H", r2[:2])[0] == 2
    assert dnsmsg.parse_answers(r1)[0][3] == "1.2.3.4"
    lines = (tmp_path / "log.csv").read_text().splitlines()
    assert lines[1].split(",")[4] == "miss" and lines[2].split(",")[4] == "hit"


def test_forwarder_prefetch_marks_hit():
    up = FakeUpstream()
    fw = CachingForwarder(up.addr)
    fw.prefetch("b.com|A")
    fw.handle(dnsmsg.build_query("b.com", qid=3), "c1")
    assert fw.stats["prefetch_hits"] == 1 and up.count == 1


def test_online_followups():
    m = OnlineFollowups(min_support=3, threshold=0.6)
    for i in range(5):
        m.observe("c", "a|A", i * 100)
        m.observe("c", "b|A", i * 100 + 1)
    assert m.predict("a|A") == [("b|A", 1.0)]
