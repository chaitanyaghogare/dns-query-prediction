"""Just enough DNS wire-format handling for a caching forwarder (RFC 1035).

Only the parts we need: read the question, find every resource record's TTL
field (so cached answers can be served with the remaining TTL), and build a
simple query packet.
"""
from __future__ import annotations

import random
import struct

QTYPE_NAMES = {1: "A", 2: "NS", 5: "CNAME", 6: "SOA", 12: "PTR", 15: "MX", 16: "TXT", 28: "AAAA", 33: "SRV", 65: "HTTPS"}
QTYPE_CODES = {v: k for k, v in QTYPE_NAMES.items()}


def _skip_name(buf: bytes, off: int) -> int:
    while True:
        n = buf[off]
        if n == 0:
            return off + 1
        if n & 0xC0 == 0xC0:          # compression pointer: 2 bytes, name ends here
            return off + 2
        off += 1 + n


def _read_name(buf: bytes, off: int) -> tuple[str, int]:
    labels, end, jumps = [], None, 0
    while True:
        n = buf[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            if end is None:
                end = off + 2
            off = ((n & 0x3F) << 8) | buf[off + 1]
            jumps += 1
            if jumps > 20:
                raise ValueError("compression loop")
            continue
        labels.append(buf[off + 1: off + 1 + n].decode("ascii", "replace"))
        off += 1 + n
    return ".".join(labels).lower(), (end if end is not None else off)


def parse_question(pkt: bytes) -> tuple[int, str, str]:
    """-> (id, qname, qtype name)."""
    if len(pkt) < 12:
        raise ValueError("short packet")
    qid, _, qd = struct.unpack("!HHH", pkt[:6])
    if qd < 1:
        raise ValueError("no question")
    name, off = _read_name(pkt, 12)
    qtype, = struct.unpack("!H", pkt[off: off + 2])
    return qid, name, QTYPE_NAMES.get(qtype, str(qtype))


def ttl_offsets(pkt: bytes) -> list[tuple[int, int]]:
    """Offsets and values of the TTL field of every answer/authority/additional record (OPT excluded)."""
    qd, an, ns, ar = struct.unpack("!HHHH", pkt[4:12])
    off = 12
    for _ in range(qd):
        off = _skip_name(pkt, off) + 4
    out = []
    for _ in range(an + ns + ar):
        off = _skip_name(pkt, off)
        rtype, _, ttl, rdlen = struct.unpack("!HHIH", pkt[off: off + 10])
        if rtype != 41:               # OPT pseudo-record has no real TTL
            out.append((off + 4, ttl))
        off += 10 + rdlen
    return out


def min_ttl(pkt: bytes, default: int = 60) -> int:
    rcode = pkt[3] & 0x0F
    vals = [t for _, t in ttl_offsets(pkt)]
    if rcode not in (0, 3) or not vals:
        return default if rcode in (0, 3) else 0
    return min(vals)


def rewrite(pkt: bytes, qid: int, elapsed: int) -> bytes:
    """Copy of a cached response with a new ID and every TTL reduced by ``elapsed`` seconds."""
    b = bytearray(pkt)
    struct.pack_into("!H", b, 0, qid)
    for off, ttl in ttl_offsets(pkt):
        struct.pack_into("!I", b, off, max(0, ttl - elapsed))
    return bytes(b)


def build_query(name: str, qtype: str = "A", qid: int | None = None) -> bytes:
    qid = random.randint(0, 0xFFFF) if qid is None else qid
    hdr = struct.pack("!HHHHHH", qid, 0x0100, 1, 0, 0, 0)      # RD=1
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.rstrip(".").split(".") if p) + b"\0"
    return hdr + qname + struct.pack("!HH", QTYPE_CODES.get(qtype, 1), 1)


def parse_answers(pkt: bytes) -> list[tuple[str, str, int, str]]:
    """(name, type, ttl, data) for answer records; A/AAAA/CNAME decoded, others as hex."""
    qd, an = struct.unpack("!HH", pkt[4:8])
    off = 12
    for _ in range(qd):
        off = _skip_name(pkt, off) + 4
    out = []
    for _ in range(an):
        name, off = _read_name(pkt, off)
        rtype, _, ttl, rdlen = struct.unpack("!HHIH", pkt[off: off + 10])
        rd = pkt[off + 10: off + 10 + rdlen]
        if rtype == 1 and rdlen == 4:
            data = ".".join(map(str, rd))
        elif rtype == 28 and rdlen == 16:
            data = ":".join(rd[i:i + 2].hex() for i in range(0, 16, 2))
        elif rtype == 5:
            data = _read_name(pkt, off + 10)[0]
        else:
            data = rd.hex()
        out.append((name, QTYPE_NAMES.get(rtype, str(rtype)), ttl, data))
        off += 10 + rdlen
    return out
