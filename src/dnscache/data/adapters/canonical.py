"""Adapter for any CSV that already has ts/client/qname[/qtype] columns.

Absolute UNIX timestamps are shifted so the trace starts at the Monday 00:00
before the first query (keeps hour-of-day features meaningful).
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..schema import Query, normalise_name


def rebase_to_monday(ts_list: list[float]) -> float:
    """Return the offset to subtract so that t=0 is the Monday 00:00 (UTC) before the first ts."""
    first = min(ts_list)
    if first < 7 * 86400:            # already relative
        return 0.0
    d = datetime.fromtimestamp(first, tz=timezone.utc)
    monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return monday.timestamp()


def load(path: str | Path, ts_col="ts", client_col="client", name_col="qname", type_col="qtype") -> list[Query]:
    rows = []
    with Path(path).open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if not r.get(name_col):
                continue
            rows.append((float(r[ts_col]), r.get(client_col, "c0"), normalise_name(r[name_col]),
                         (r.get(type_col) or "A").upper()))
    off = rebase_to_monday([r[0] for r in rows]) if rows else 0.0
    qs = [Query(t - off, c, n, ty) for t, c, n, ty in rows]
    qs.sort(key=lambda q: q.ts)
    return qs
