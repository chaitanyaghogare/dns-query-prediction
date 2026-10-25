"""Quick quality check of a trace against what the experiments need."""
from __future__ import annotations

from collections import Counter

from .schema import Query


def assess(queries: list[Query]) -> dict:
    if not queries:
        return {"ok": False, "problems": ["empty trace"]}
    keys = Counter(q.key for q in queries)
    duration_h = (queries[-1].ts - queries[0].ts) / 3600
    repeated = sum(c for c in keys.values() if c > 1)
    report = {
        "rows": len(queries),
        "duration_h": round(duration_h, 2),
        "clients": len({q.client for q in queries}),
        "distinct_keys": len(keys),
        "share_of_lookups_on_repeated_keys": round(repeated / len(queries), 3),
        "top_10": keys.most_common(10),
    }
    problems = []
    if duration_h < 6:
        problems.append("shorter than 6 h: too little history for train/val/test windows")
    if report["share_of_lookups_on_repeated_keys"] < 0.5:
        problems.append("most lookups are one-off names; caching cannot help much")
    report["problems"] = problems
    report["ok"] = not problems
    return report
