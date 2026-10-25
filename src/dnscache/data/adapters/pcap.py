"""Adapter for raw packet captures (.pcap, .pcapng, optionally .gz), stdlib only.

Reads every UDP packet on port 53 and keeps:

* queries (QR=0)  -> one ``Query`` each: capture time, source IP, name, type
* responses (QR=1, NOERROR/NXDOMAIN) -> the TTL of each cache key. A resolver
  answers with the *remaining* TTL, so the largest value seen is kept; it is
  a lower bound of the real TTL.

Used for the Thapar campus trace (hourly pcaps). No Wireshark needed.

    python -m dnscache.data.adapters.pcap data/thapar/raw data/thapar --sample 0.1 --utc-offset 5.5 --exclude 172.31.1.6
"""
from __future__ import annotations

import argparse
import gzip
import json
import struct
import zlib
from pathlib import Path

from ...collect.dnsmsg import min_ttl, parse_question
from ..schema import Query, cache_key, normalise_name
from .canonical import rebase_to_monday

PCAP_MAGIC = {b"\xd4\xc3\xb2\xa1": ("<", 1e-6), b"\xa1\xb2\xc3\xd4": (">", 1e-6),
              b"\x4d\x3c\xb2\xa1": ("<", 1e-9), b"\xa1\xb2\x3c\x4d": (">", 1e-9)}
PCAPNG_MAGIC = b"\x0a\x0d\x0d\x0a"
LINK_ETHERNET, LINK_RAW, LINK_SLL, LINK_IPV4, LINK_IPV6 = 1, 101, 113, 228, 229
SUFFIXES = (".pcap", ".pcapng", ".cap", ".pcap.gz", ".pcapng.gz")


def _open(path: Path):
    return gzip.open(path, "rb") if path.suffix == ".gz" else path.open("rb")


def _frames_pcap(f, head: bytes):
    """-> (ts, linktype, frame) for a classic pcap file."""
    e, unit = PCAP_MAGIC[head[:4]]
    linktype, = struct.unpack(e + "I", head[20:24])
    while True:
        rec = f.read(16)
        if len(rec) < 16:
            return
        sec, frac, incl, _ = struct.unpack(e + "IIII", rec)
        data = f.read(incl)
        if len(data) < incl:
            return
        yield sec + frac * unit, linktype & 0xFFFF, data


def _frames_pcapng(f, head: bytes):
    """-> (ts, linktype, frame) for a pcapng file (enhanced and simple packet blocks)."""
    e = "<" if head[8:12] == b"\x4d\x3c\x2b\x1a" else ">"
    blen, = struct.unpack(e + "I", head[4:8])
    f.read(blen - len(head))                      # rest of the section header
    ifaces: list[tuple[int, float]] = []          # (linktype, seconds per tick)
    while True:
        bh = f.read(8)
        if len(bh) < 8:
            return
        btype, blen = struct.unpack(e + "II", bh)
        body = f.read(blen - 8)
        if btype == 0x0A0D0D0A:                   # a new section starts over
            e = "<" if body[0:4] == b"\x4d\x3c\x2b\x1a" else ">"
            ifaces = []
        elif btype == 1:                          # interface description
            linktype, = struct.unpack(e + "H", body[0:2])
            unit, off = 1e-6, 8
            while off + 4 <= len(body) - 4:
                code, olen = struct.unpack(e + "HH", body[off:off + 4])
                if code == 0:
                    break
                if code == 9:                     # if_tsresol
                    r = body[off + 4]
                    unit = 2.0 ** -(r & 0x7F) if r & 0x80 else 10.0 ** -r
                off += 4 + (olen + 3) // 4 * 4
            ifaces.append((linktype, unit))
        elif btype == 6:                          # enhanced packet
            iid, hi, lo, cap = struct.unpack(e + "IIII", body[:16])
            lt, unit = ifaces[iid] if iid < len(ifaces) else (LINK_ETHERNET, 1e-6)
            yield ((hi << 32) | lo) * unit, lt, body[20:20 + cap]
        elif btype == 3 and ifaces:               # simple packet: no timestamp, skip
            continue


def frames(path: Path):
    with _open(path) as f:
        head = f.read(24)
        if head[:4] in PCAP_MAGIC:
            yield from _frames_pcap(f, head)
        elif head[:4] == PCAPNG_MAGIC:
            yield from _frames_pcapng(f, head)
        else:
            raise ValueError(f"{path.name}: not a pcap/pcapng file")


def dns_payload(linktype: int, frame: bytes) -> tuple[str, bytes] | None:
    """-> (source IP, DNS message) if the frame is UDP to or from port 53."""
    if linktype == LINK_ETHERNET:
        etype, off = struct.unpack("!H", frame[12:14])[0], 14
        while etype in (0x8100, 0x88A8):          # VLAN tags
            etype, off = struct.unpack("!H", frame[off + 2:off + 4])[0], off + 4
    elif linktype == LINK_SLL:
        etype, off = struct.unpack("!H", frame[14:16])[0], 16
    elif linktype in (LINK_RAW, LINK_IPV4, LINK_IPV6):
        etype, off = (0x0800 if frame[0] >> 4 == 4 else 0x86DD), 0
    else:
        return None
    if etype == 0x0800:
        ihl = (frame[off] & 0x0F) * 4
        if frame[off + 9] != 17 or struct.unpack("!H", frame[off + 6:off + 8])[0] & 0x1FFF:
            return None                           # not UDP, or a later IP fragment
        src = ".".join(map(str, frame[off + 12:off + 16]))
        off += ihl
    elif etype == 0x86DD:
        if frame[off + 6] != 17:
            return None
        raw = frame[off + 8:off + 24]
        src = ":".join(raw[i:i + 2].hex() for i in range(0, 16, 2))
        off += 40
    else:
        return None
    sport, dport = struct.unpack("!HH", frame[off:off + 4])
    if 53 not in (sport, dport):
        return None
    return src, frame[off + 8:]


def sampled(client: str, fraction: float) -> bool:
    """Deterministic client sample: the same IPs are kept on every run."""
    return fraction >= 1 or zlib.crc32(client.encode()) % 10_000 < fraction * 10_000


def pcap_files(path: str | Path) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    return sorted(x for x in p.rglob("*") if x.is_file() and x.name.lower().endswith(SUFFIXES))


def load(path: str | Path, sample: float = 1.0, utc_offset_h: float = 0.0, exclude=(),
         progress=None) -> tuple[list[Query], dict[str, int]]:
    """Read one capture or every capture under a folder -> (queries, ttl by cache key).

    ``exclude``: source IPs whose queries are not client demand, e.g. the
    campus resolver forwarding its own misses upstream.
    """
    exclude = set(exclude)
    files = pcap_files(path)
    if not files:
        raise FileNotFoundError(f"no .pcap/.pcapng files under {path}: download the captures and put them there")
    rows, ttl = [], {}
    for i, fp in enumerate(files, 1):
        n0 = len(rows)
        for ts, lt, frame in frames(fp):
            try:
                hit = dns_payload(lt, frame)
                if hit is None:
                    continue
                src, msg = hit
                qr, rcode = msg[2] >> 7, msg[3] & 0x0F
                _, name, qtype = parse_question(msg)
                if not name:
                    continue
                if qr == 0:
                    if src not in exclude and sampled(src, sample):
                        rows.append((ts, src, normalise_name(name), qtype))
                elif rcode in (0, 3):
                    k = cache_key(name, qtype)
                    t = min_ttl(msg)
                    if t > ttl.get(k, -1):
                        ttl[k] = t
            except (IndexError, ValueError, struct.error):
                continue                          # truncated or malformed packet
        if progress:
            progress(f"{fp.name}: {len(rows) - n0} queries ({i}/{len(files)})")
    shift = utc_offset_h * 3600                   # local time, so hour-of-day is meaningful
    rows = [(t + shift, c, n, ty) for t, c, n, ty in rows]
    off = rebase_to_monday([r[0] for r in rows]) if rows else 0.0
    qs = sorted((Query(t - off, c, n, ty) for t, c, n, ty in rows), key=lambda q: q.ts)
    return qs, {k: v for k, v in ttl.items() if v > 0}


def convert(src: str | Path, out_dir: str | Path, sample: float = 1.0, utc_offset_h: float = 0.0,
            exclude=(), progress=print) -> tuple[list[Query], dict[str, int]]:
    """pcaps -> ``out_dir/trace.csv`` + ``out_dir/catalogue.json`` (the files run.py reads)."""
    from .. import io
    qs, ttl = load(src, sample, utc_offset_h, exclude, progress)
    out = Path(out_dir)
    io.write_trace(out / "trace.csv", qs, {"source": f"pcap:{Path(src).name}", "client_sample": sample,
                                          "utc_offset_h": utc_offset_h, "excluded_clients": sorted(exclude),
                                          "ttl_keys": len(ttl)})
    (out / "catalogue.json").write_text(json.dumps({"ttl": ttl, "ttl_source": "max TTL seen in responses"}))
    return qs, ttl


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="a capture file or a folder of captures")
    ap.add_argument("out", help="output folder, e.g. data/thapar")
    ap.add_argument("--sample", type=float, default=1.0, help="fraction of clients to keep (0-1)")
    ap.add_argument("--utc-offset", type=float, default=0.0, help="hours to add to capture times (IST = 5.5)")
    ap.add_argument("--exclude", nargs="*", default=[], help="source IPs to drop (e.g. the campus resolver)")
    a = ap.parse_args(argv)
    qs, ttl = convert(a.src, a.out, a.sample, a.utc_offset, a.exclude)
    print(f"{len(qs)} queries, {len({q.client for q in qs})} clients, {len(ttl)} TTLs -> {a.out}")


if __name__ == "__main__":
    main()
