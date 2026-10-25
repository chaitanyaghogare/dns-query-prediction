"""Build the offline dashboard: one self-contained HTML file, no server needed.

    python -m dnscache.dashboard.build [--summary experiments/results/synth/summary.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = Path(__file__).with_name("template.html")


def findings(summary: dict) -> list[dict]:
    res = {r["policy"]: r for r in summary["results"] if r["capacity"] is None}
    b, f, riu = res["ttl_only"], res["final"], res["refresh_if_used"]
    ml, fo, orc = res["ml_refresh"], res["followup_only"], res["oracle_refresh"]
    pct = lambda x: f"{x * 100:.1f}%"
    pts = lambda a, c: f"{(a - c) * 100:+.1f} pts"
    out = [
        {"kind": "Measured", "text": f"The final AI-assisted cache answered {pct(f['hit_rate'])} of test-window lookups from cache, "
                                     f"against {pct(b['hit_rate'])} for the normal TTL cache ({pts(f['hit_rate'], b['hit_rate'])})."},
        {"kind": "Measured", "text": f"Mean lookup latency fell from {b['mean_ms']:.1f} ms to {f['mean_ms']:.1f} ms "
                                     f"and P95 from {b['p95_ms']:.0f} ms to {f['p95_ms']:.0f} ms."},
        {"kind": "Measured", "text": f"The price is background traffic: {f['upstream_per_lookup']:.3f} upstream queries per lookup "
                                     f"instead of {b['upstream_per_lookup']:.3f}. {f['wasted_prefetches']} of "
                                     f"{f['upstream_prefetch']} prefetches were never used "
                                     f"(precision {pct(f['prefetch_precision'] or 0)})."},
        {"kind": "Measured", "text": f"Compared with the simple refresh-if-used rule ({pct(riu['hit_rate'])} hits at "
                                     f"{riu['upstream_per_lookup']:.3f} upstream/lookup), the final system gets "
                                     f"{pts(f['hit_rate'], riu['hit_rate'])} at "
                                     f"{abs(f['upstream_per_lookup'] / riu['upstream_per_lookup'] - 1) * 100:.0f}% "
                                     f"{'more' if f['upstream_per_lookup'] > riu['upstream_per_lookup'] else 'less'} upstream traffic."},
        {"kind": "Interpretation", "text": f"Most of the gain comes from the follow-up (transition probability) model: on its own it reaches "
                                           f"{pct(fo['hit_rate'])}. The refresh tree alone reaches {pct(ml['hit_rate'])}, "
                                           f"and mainly decides which expiring entries are worth renewing."},
        {"kind": "Interpretation", "text": f"The refresh-only oracle ({pct(orc['hit_rate'])}) is below the final system because it cannot "
                                           f"help first lookups of a name; follow-up prefetching can."},
        {"kind": "Hypothesis", "text": "On real traffic the gain is likely smaller: the synthetic workload has clean page-load structure, "
                                       "and real traces contain many more one-off names."},
    ]
    verdict = "Met" if f["hit_rate"] > b["hit_rate"] + 0.05 and (f["prefetch_precision"] or 0) >= 0.6 else "Unclear"
    out.append({"kind": "Verdict", "text": f"{verdict}: prediction improves hit rate and latency in simulation, "
                                           f"while keeping most prefetches useful."})
    return out


def build(summary: dict, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    data = dict(summary)
    data["findings"] = findings(summary)
    data.pop("config", None)
    data["config_name"] = summary["config"]["name"]
    blob = json.dumps(data, separators=(",", ":"), default=float).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", default=str(ROOT / "experiments/results/synth/summary.json"))
    ap.add_argument("--out", default=str(ROOT / "dashboard/index.html"))
    a = ap.parse_args(argv)
    print(build(json.loads(Path(a.summary).read_text()), a.out))


if __name__ == "__main__":
    main()
