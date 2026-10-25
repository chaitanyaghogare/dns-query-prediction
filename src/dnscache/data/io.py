"""Read and write traces in the canonical CSV format, plus a JSON manifest."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from .schema import CSV_COLUMNS, SCHEMA_VERSION, Query, normalise_name


def write_trace(path: str | Path, queries: list[Query], meta: dict | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for q in queries:
            w.writerow([f"{q.ts:.3f}", q.client, q.qname, q.qtype])
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "rows": len(queries),
        "duration_s": round(queries[-1].ts - queries[0].ts, 3) if queries else 0,
        "clients": len({q.client for q in queries}),
        "domains": len({q.key for q in queries}),
        **(meta or {}),
    }
    path.with_suffix(".manifest.json").write_text(json.dumps(manifest, indent=2))


def read_trace(path: str | Path) -> list[Query]:
    out = []
    with Path(path).open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(Query(float(row["ts"]), row["client"],
                             normalise_name(row["qname"]), (row.get("qtype") or "A").upper()))
    out.sort(key=lambda q: q.ts)
    return out


def read_manifest(path: str | Path) -> dict:
    p = Path(path).with_suffix(".manifest.json")
    return json.loads(p.read_text()) if p.exists() else {}
