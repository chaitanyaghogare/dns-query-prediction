"""Write the demo outputs: CSV logs, summary.json and a self-contained report.html."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path


def prediction_accuracy(preds, results, window_s: float) -> dict:
    """A prediction is correct if the same client really looked the domain up within window_s."""
    by_client = {}
    for r in results:
        by_client.setdefault(r.client, []).append(r)
    correct = 0
    for p in preds:
        correct += any(r.domain == p.domain and p.t < r.t <= p.t + window_s for r in by_client.get(p.client, []))
    return {"predictions": len(preds), "correct": correct, "accuracy": correct / len(preds) if preds else None}


def write_outputs(out: Path, meta: dict, normal, ai, model, window_s: float) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "lookups.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "client", "domain", "normal_result", "normal_ms", "ai_result", "ai_ms",
                    "ai_from_prefetch", "ttl_s", "addresses"])
        for a, b in zip(normal.results, ai.results):
            w.writerow([f"{a.t:.2f}", a.client, a.domain, a.outcome, f"{a.ms:.2f}", b.outcome, f"{b.ms:.2f}",
                        int(b.from_prefetch), b.ttl_left, " ".join(b.addresses)])
    with open(out / "predictions.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "client", "after_domain", "predicted_domain", "confidence", "action", "used", "reason"])
        for p in ai.predictions:
            w.writerow([f"{p.t:.2f}", p.client, p.after, p.domain, f"{p.prob:.2f}", p.action, int(p.used), p.reason])
    rules = sorted(((a.split("|")[0], b.split("|")[0], p) for a, fol in model.table().items() for b, p in fol),
                   key=lambda x: -x[2])
    summary = {
        **meta,
        "normal": normal.summary(),
        "ai": ai.summary(),
        "prediction_accuracy": prediction_accuracy(ai.predictions, ai.results, window_s),
        "rules_learned": len(rules),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "report.html").write_text(render(summary, normal, ai, rules), encoding="utf-8")
    return summary


def _pct(x):
    return "-" if x is None else f"{x * 100:.1f}%"


def comparison_rows(s: dict) -> list[tuple[str, str, str]]:
    n, a, acc = s["normal"], s["ai"], s["prediction_accuracy"]
    return [
        ("Lookups", f"{n['lookups']}", f"{a['lookups']}"),
        ("Answered from cache (hit rate)", f"{n['hits']} ({_pct(n['hit_rate'])})", f"{a['hits']} ({_pct(a['hit_rate'])})"),
        ("Waited for a prefetch already on its way", f"{n['waits']}", f"{a['waits']}"),
        ("Cache misses", f"{n['misses']}", f"{a['misses']}"),
        ("Average lookup time", f"{n['mean_ms']:.1f} ms", f"{a['mean_ms']:.1f} ms"),
        ("95th percentile lookup time", f"{n['p95_ms']:.1f} ms", f"{a['p95_ms']:.1f} ms"),
        ("Total time users waited for DNS", f"{n['total_wait_ms'] / 1000:.2f} s", f"{a['total_wait_ms'] / 1000:.2f} s"),
        ("Upstream queries for misses", f"{n['upstream_lookups']}", f"{a['upstream_lookups']}"),
        ("Upstream queries for prefetches", "0", f"{a['upstream_prefetches']}"),
        ("Upstream queries in total", f"{n['upstream_total']}", f"{a['upstream_total']}"),
        ("Prefetches used / unnecessary", "-", f"{a['prefetches_used']} / {a['unnecessary_prefetches']}"),
        ("Prediction accuracy (domain really requested within 30 s)", "-",
         f"{acc['correct']} of {acc['predictions']} ({_pct(acc['accuracy'])})"),
    ]


CSS = """
:root{color-scheme:light;--bg:#f6f5f2;--s:#fcfcfb;--b:#e3e1db;--g:#ecebe6;--t:#0b0b0b;--t2:#52514e;--m:#85837d;
--base:#eb6834;--ai:#2a78d6;--soft:#e6effa;--good:#1f7a3a;--goodbg:#e3f3e7;--bad:#b42318;--badbg:#fbe7e5;--w:#8a5a00;--wbg:#fbf0d9;--diff:#fff7df}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){color-scheme:dark;--bg:#121211;--s:#1a1a19;--b:#2f2e2b;--g:#262523;--t:#f0efec;--t2:#c3c2b7;--m:#8f8d85;
--base:#d95926;--ai:#3987e5;--soft:#1d2a3b;--good:#6cc785;--goodbg:#183222;--bad:#f08a80;--badbg:#3a1c19;--w:#e8b75a;--wbg:#3a2e14;--diff:#2a2614}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:14px/1.45 system-ui,"Segoe UI",Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:0 0 4px}
.sub,.note{color:var(--t2)}.note{font-size:12px;margin:0 0 10px}
.card{background:var(--s);border:1px solid var(--b);border-radius:10px;padding:14px;margin-top:14px;min-width:0}
.grid{display:grid;gap:14px;grid-template-columns:repeat(4,minmax(0,1fr))}@media(max-width:900px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
.k .v{font-size:22px;font-weight:650}.k .l{font-size:12px;color:var(--t2)}.who{font-size:11px;color:var(--m)}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:4px}
.wrap{overflow:auto;max-height:480px}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:5px 8px;border-bottom:1px solid var(--g);white-space:nowrap}th{font-size:12px;color:var(--t2);position:sticky;top:0;background:var(--s)}
.num{text-align:right}tr.diff td{background:var(--diff)}
.b{display:inline-block;font-size:11px;font-weight:600;padding:1px 6px;border-radius:4px;min-width:42px;text-align:center}
.HIT{background:var(--goodbg);color:var(--good)}.MISS{background:var(--badbg);color:var(--bad)}.WAIT{background:var(--wbg);color:var(--w)}
.bar{height:8px;background:var(--g);border-radius:4px;width:90px;display:inline-block;vertical-align:middle;margin-right:6px}.bar i{display:block;height:100%;background:var(--ai);border-radius:4px}
.flow{display:flex;gap:10px;flex-wrap:wrap;align-items:stretch}.flow div{flex:1;min-width:180px;background:var(--bg);border-radius:8px;padding:10px}
.flow b{display:block;margin-bottom:4px}
"""


def render(s: dict, normal, ai, rules) -> str:
    e = html.escape
    n, a = s["normal"], s["ai"]
    kp = lambda label, bv, av: (f'<div class="card k"><div class="l">{label}</div><div style="display:flex;gap:16px;margin-top:6px">'
                                f'<div><div class="v">{bv}</div><div class="who"><i class="dot" style="background:var(--base)"></i>Normal cache</div></div>'
                                f'<div><div class="v">{av}</div><div class="who"><i class="dot" style="background:var(--ai)"></i>AI cache</div></div></div></div>')
    kpis = (kp("Hit rate", _pct(n["hit_rate"]), _pct(a["hit_rate"])) +
            kp("Average lookup time", f"{n['mean_ms']:.1f} ms", f"{a['mean_ms']:.1f} ms") +
            kp("Upstream DNS queries", n["upstream_total"], a["upstream_total"]) +
            kp("Prediction accuracy", "-", _pct(s["prediction_accuracy"]["accuracy"])))
    cmp = "".join(f"<tr><td>{e(k)}</td><td class=num>{e(v1)}</td><td class=num>{e(v2)}</td></tr>" for k, v1, v2 in comparison_rows(s))
    lk = []
    for x, y in zip(normal.results, ai.results):
        tag = " (prefetched)" if y.from_prefetch and y.outcome != "MISS" else ""
        lk.append(f'<tr class="{"diff" if x.outcome != y.outcome else ""}"><td class=num>{x.t:.1f}</td><td>{e(x.client)}</td><td>{e(x.domain)}</td>'
                  f'<td><span class="b {x.outcome}">{x.outcome}</span></td><td class=num>{x.ms:.1f}</td>'
                  f'<td><span class="b {y.outcome}">{y.outcome}</span>{tag}</td><td class=num>{y.ms:.1f}</td>'
                  f'<td class=num>{y.ttl_left}</td><td>{e(", ".join(y.addresses[:2]))}</td></tr>')
    pr = "".join(f'<tr><td class=num>{p.t:.1f}</td><td>{e(p.client)}</td><td>{e(p.after)}</td><td><b>{e(p.domain)}</b></td>'
                 f'<td><span class="bar"><i style="width:{p.prob * 100:.0f}%"></i></span>{p.prob * 100:.0f}%</td><td>{e(p.action)}</td>'
                 f'<td>{"yes" if p.used else ("-" if p.action != "prefetched" else "no")}</td><td>{e(p.reason)}</td></tr>' for p in ai.predictions)
    ru = "".join(f"<tr><td>{e(x)}</td><td>{e(y)}</td><td><span class='bar'><i style='width:{p * 100:.0f}%'></i></span>{p * 100:.0f}%</td></tr>"
                 for x, y, p in rules[:40])
    up = s["upstream"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DNS Demo Report</title><style>{CSS}</style></head><body><main>
<h1>DNS cache demo: normal cache vs AI-assisted cache</h1>
<p class="sub">Run {e(s['started'])} · upstream DNS server: <b>{e(up)}</b>{' (NOT a real network run)' if 'offline' in up else ' (real DNS queries)'} · confidence threshold {s['threshold']}</p>
<div class="card"><h2>What went in and what came out</h2><div class="flow">
<div><b>Input 1: history</b>{e(s['history_file'])}<br>{s['history_lookups']:,} past lookups from {s['history_clients']} clients</div>
<div><b>Learning</b>{s['rules_from_history']} follow-up rules learned from the history ({s['rules_learned']} after also learning during this run): "after domain A, domain B is requested within {s['window_s']:.0f} s with probability p"</div>
<div><b>Input 2: lookups to answer</b>{e(s['session_file'])}<br>{n['lookups']} lookups, fed to both caches in the same order</div>
<div><b>Output</b>per-lookup results, predictions with confidence, prefetches and the comparison below</div></div></div>
<div class="grid">{kpis}</div>
<div class="card"><h2>Comparison</h2><p class="note">Both caches answered exactly the same lookups using the same upstream server.</p>
<div class="wrap"><table><thead><tr><th>Metric</th><th class=num>Normal cache</th><th class=num>AI cache</th></tr></thead><tbody>{cmp}</tbody></table></div></div>
<div class="card"><h2>Every lookup</h2><p class="note">Highlighted rows are where the AI cache answered differently. TTL = seconds the answer stays valid. WAIT = the prefetch was already on its way.</p>
<div class="wrap"><table><thead><tr><th class=num>Time (s)</th><th>Client</th><th>Domain</th><th>Normal</th><th class=num>ms</th><th>AI cache</th><th class=num>ms</th><th class=num>TTL</th><th>Address</th></tr></thead><tbody>{''.join(lk)}</tbody></table></div></div>
<div class="card"><h2>Predictions and prefetches</h2><p class="note">Made right after a lookup. "used" = the prefetched answer later served a real lookup before its TTL ran out.</p>
<div class="wrap"><table><thead><tr><th class=num>Time (s)</th><th>Client</th><th>After</th><th>Predicted next</th><th>Confidence</th><th>Action</th><th>Used</th><th>Why</th></tr></thead><tbody>{pr}</tbody></table></div></div>
<div class="card"><h2>Strongest learned rules</h2><div class="wrap"><table><thead><tr><th>After</th><th>Comes</th><th>Probability</th></tr></thead><tbody>{ru}</tbody></table></div></div>
</main></body></html>"""
