"""Adapter for DNS queries exported from Wireshark/tshark.

Export with:
    tshark -r capture.pcap -Y "dns.flags.response == 0" -T fields -E separator=, -E header=y \
        -e frame.time_epoch -e ip.src -e dns.qry.name -e dns.qry.type > queries.csv
"""
from __future__ import annotations

import csv
from pathlib import Path

from ..schema import Query, normalise_name
from .canonical import rebase_to_monday

QTYPES = {"1": "A", "28": "AAAA", "5": "CNAME", "15": "MX", "16": "TXT", "65": "HTTPS", "33": "SRV", "12": "PTR"}


def load(path: str | Path) -> list[Query]:
    rows = []
    with Path(path).open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = (r.get("dns.qry.name") or "").split(",")[0]
            if not name:
                continue
            qt = (r.get("dns.qry.type") or "1").split(",")[0]
            rows.append((float(r["frame.time_epoch"]), r.get("ip.src") or r.get("ipv6.src") or "?",
                         normalise_name(name), QTYPES.get(qt, qt)))
    off = rebase_to_monday([r[0] for r in rows]) if rows else 0.0
    qs = [Query(t - off, c, n, ty) for t, c, n, ty in rows]
    qs.sort(key=lambda q: q.ts)
    return qs
