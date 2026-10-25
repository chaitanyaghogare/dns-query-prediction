"""Reproduce everything: data -> models -> policy comparison -> dashboard.

    python -m dnscache.run                      # default synthetic experiment
    python -m dnscache.run --config configs/my_trace.json

Chronological split (no shuffling): the models are trained on the train
window, thresholds are checked on the validation window, and every headline
number comes from the test window, which the models never saw.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np

from .data import io
from .data.adapters import canonical
from .data.assess import assess
from .predict import models as M
from .predict.features import FEATURES, FutureIndex
from .predict.sequence import SequenceModel
from .sim import policies as P
from .sim.environment import Upstream
from .sim.simulator import Simulator
from .synth.generator import SynthConfig, generate, save_catalogue

ROOT = Path(__file__).resolve().parents[2]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_config(path: str | Path | None) -> dict:
    cfg = json.loads((ROOT / "configs" / "default.json").read_text())
    if path:
        cfg.update(json.loads(Path(path).read_text()))
    return cfg


def load_data(cfg: dict):
    name = cfg["name"]
    if cfg.get("trace_csv"):
        queries = canonical.load(ROOT / cfg["trace_csv"])
        ttl = {}
        if cfg.get("ttl_csv"):
            with open(ROOT / cfg["ttl_csv"], newline="") as f:
                ttl = {r["key"]: int(float(r["ttl"])) for r in csv.DictReader(f)}
        return queries, ttl
    trace = ROOT / "data" / name / "trace.csv"
    cat = ROOT / "data" / name / "catalogue.json"
    if trace.exists() and cat.exists():
        return io.read_trace(trace), json.loads(cat.read_text())["ttl"]
    scfg = SynthConfig(seed=cfg["seed"], **cfg.get("synth", {}))
    queries, ttl = generate(scfg)
    io.write_trace(trace, queries, {"source": "synthetic", "seed": scfg.seed})
    save_catalogue(cat, ttl, scfg)
    return queries, ttl


def run_policy(queries, up, policy, cfg, window, capacity=None, future=None, record=None) -> Simulator:
    sim = Simulator(queries, up, policy, capacity=capacity, window=window,
                    decision_lead=cfg["decision_lead_s"], record=record, future=future)
    sim.run()
    return sim


def collect(queries, up, inner, cfg, until, future):
    col = P.Collector(inner)
    run_policy(queries, up, col, cfg, (0, until), future=future)
    return col.rows


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="JSON file overriding configs/default.json")
    ap.add_argument("--no-dashboard", action="store_true")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    name = cfg["name"]
    H = 3600
    w = {k: (v[0] * H, v[1] * H) for k, v in cfg["windows_h"].items()}
    out_dir = ROOT / "experiments" / "results" / name
    model_dir = ROOT / "models" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    # 1. data -------------------------------------------------------------
    queries, ttl = load_data(cfg)
    quality = assess(queries)
    log(f"trace: {quality['rows']} lookups, {quality['distinct_keys']} names, {quality['duration_h']} h")
    uc = cfg["upstream"]
    up = Upstream(ttl, default_ttl=uc["default_ttl"], seed=cfg["seed"], median_ms=uc["median_ms"],
                  slow_prob=uc["slow_prob"])
    future = FutureIndex(queries)
    train_q = [q for q in queries if w["train"][0] <= q.ts < w["train"][1]]

    # 2. follow-up (transition probability) model ------------------------
    fc = cfg["followup_model"]
    seq = SequenceModel(fc["window_s"], fc["min_support"]).fit(train_q)
    seq.save(model_dir / "sequence.json")
    top_pairs = sorted(((a, b, p) for a, fol in seq.table().items() for b, p in fol), key=lambda x: -x[2])
    log(f"follow-up model: {len(seq.table())} names with predictable followers")

    # 3. refresh model: decision dataset + DAgger-style rounds ------------
    rc = cfg["refresh_model"]
    until = w["validation"][1]
    rows = [(*r, 0) for r in collect(queries, up, P.RefreshIfUsed(), cfg, until, future)]
    ml_rounds = []
    for rnd in range(rc["dagger_rounds"] + 1):
        tr = [r for r in rows if w["train"][0] <= r[0] < w["train"][1]]
        va = [r for r in rows if w["validation"][0] <= r[0] < w["validation"][1]]
        X = lambda rs: np.array([r[2] for r in rs], dtype=float)
        Y = lambda rs: np.array([r[3] for r in rs], dtype=int)
        fitted, report = M.train_and_evaluate(X(tr), Y(tr), X(va), Y(va), rc["threshold"], cfg["seed"])
        ml_rounds.append({"round": rnd, "train_rows": len(tr), "validation_rows": len(va), "models": report})
        log(f"refresh model round {rnd}: {len(tr)} train rows, tree F1={report['decision_tree']['f1']:.3f}")
        tree = fitted["decision_tree"]
        if rnd < rc["dagger_rounds"]:
            more = collect(queries, up, P.ExpiryRefreshPolicy(M.TreePredictor(tree), rc["threshold"]), cfg, until, future)
            rows += [(*r, rnd + 1) for r in more]
    with open(ROOT / "data" / name / "decisions.csv", "w", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["t", "key", *FEATURES, "label", "round"])
        for t, k, x, y, rnd in rows:
            wr.writerow([round(t, 2), k, *[round(v, 3) for v in x], y, rnd])
    M.save(tree, model_dir / "refresh_tree.pkl")
    rules = M.tree_rules(tree)
    (model_dir / "tree_rules.txt").write_text(rules)
    predictor = M.TreePredictor(tree)

    # 4. pick the operating threshold on the validation window ------------
    #    lowest threshold whose prefetches are still mostly useful
    fthr = fc["threshold"]
    val_sweep = []
    for t in cfg["threshold_sweep"]:
        s = run_policy(queries, up, P.ExpiryRefreshPolicy(predictor, t, seq, fthr), cfg, w["validation"]).m.summary()
        val_sweep.append({"threshold": t, "hit_rate": s["hit_rate"], "upstream_per_lookup": s["upstream_per_lookup"],
                          "prefetch_precision": s["prefetch_precision"]})
    ok = [v["threshold"] for v in val_sweep if (v["prefetch_precision"] or 0) >= rc["min_prefetch_precision"]]
    thr = min(ok) if ok else rc["threshold"]
    log(f"chosen refresh threshold on validation: {thr} (prefetch precision >= {rc['min_prefetch_precision']})")

    # 5. policy comparison on the test window -----------------------------

    def policies():
        return [
            P.TTLOnly(), P.UnboundPrefetch(), P.RefreshIfUsed(), P.FollowupOnly(seq, fthr),
            P.ExpiryRefreshPolicy(predictor, thr),
            P.ExpiryRefreshPolicy(predictor, thr, seq, fthr, name="final",
                                  label=f"Final: ML refresh (p>={thr}) + follow-up (p>={fthr})"),
            P.OracleRefresh(),
        ]

    rs = cfg["replay_start_h"] * H
    record = (rs, rs + cfg["replay_minutes"] * 60)
    results, sims = [], {}
    for cap in cfg["capacities"]:
        for pol in policies():
            t0 = time.time()
            sim = run_policy(queries, up, pol, cfg, w["test"], cap, future, record if cap is None else None)
            s = sim.m.summary()
            s.update(policy=pol.name, label=pol.label, capacity=cap, runtime_s=round(time.time() - t0, 2))
            results.append(s)
            if cap is None:
                sims[pol.name] = sim
            log(f"{pol.name:18s} cap={cap}: hit {s['hit_rate']:.1%}  mean {s['mean_ms']:.1f} ms  "
                f"upstream/lookup {s['upstream_per_lookup']:.3f}")

    sweep = []
    for t in cfg["threshold_sweep"]:
        for fu in (False, True):
            pol = P.ExpiryRefreshPolicy(predictor, t, seq if fu else None, fthr)
            s = run_policy(queries, up, pol, cfg, w["test"], None, future).m.summary()
            sweep.append({"threshold": t, "followup": fu, **{k: s[k] for k in
                          ("hit_rate", "mean_ms", "p95_ms", "upstream_per_lookup", "prefetch_precision", "wasted_prefetches")}})

    # follow-up prediction accuracy on the test window (offline view)
    test_q = [q for q in queries if w["test"][0] <= q.ts < w["test"][1]]
    seq_eval = evaluate_sequence(seq, test_q, fthr)

    # 6. write everything -------------------------------------------------
    replay = build_replay(sims["ttl_only"], sims["final"])
    summary = {
        "config": cfg, "quality": quality, "features": FEATURES,
        "windows_h": cfg["windows_h"], "results": results, "threshold_sweep": sweep,
        "ml_rounds": ml_rounds, "chosen_threshold": thr, "validation_sweep": val_sweep, "tree_rules": rules,
        "feature_importance": M.feature_importance(tree),
        "followup": {"names": len(seq.table()), "top_pairs": [
            {"a": a.split("|")[0], "b": b.split("|")[0], "p": round(p, 3)} for a, b, p in top_pairs[:15]],
            "test_eval": seq_eval},
        "replay": replay,
        "generated": time.strftime("%Y-%m-%d %H:%M"),
    }
    for r in results:
        r["hourly_hit_rate"] = {str(k): v for k, v in r["hourly_hit_rate"].items()}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=float))
    with open(out_dir / "comparison.csv", "w", newline="") as f:
        cols = ["policy", "capacity", "hit_rate", "inflight_rate", "miss_rate", "mean_ms", "p50_ms", "p95_ms",
                "p99_ms", "upstream_demand", "upstream_prefetch", "upstream_per_lookup", "prefetch_precision",
                "wasted_prefetches", "mean_occupancy"]
        wr = csv.DictWriter(f, cols, extrasaction="ignore")
        wr.writeheader()
        wr.writerows(results)
    log(f"results written to {out_dir.relative_to(ROOT)}")

    if not args.no_dashboard:
        from .dashboard.build import build
        path = build(summary, ROOT / "dashboard" / "index.html")
        log(f"dashboard written to {path.relative_to(ROOT)}")
    return summary


def evaluate_sequence(seq: SequenceModel, queries, threshold: float) -> dict:
    """Of the follow-ups predicted with p >= threshold, how many did the same client actually ask for?"""
    by_client: dict[str, list] = {}
    for q in queries:
        by_client.setdefault(q.client, []).append(q)
    predicted = correct = 0
    for qs in by_client.values():
        for i, q in enumerate(qs):
            preds = seq.predict(q.key, threshold)
            if not preds:
                continue
            nxt = set()
            for q2 in qs[i + 1:]:
                if q2.ts - q.ts > seq.window_s:
                    break
                nxt.add(q2.key)
            predicted += len(preds)
            correct += sum(1 for b, _ in preds if b in nxt)
    return {"threshold": threshold, "predictions": predicted, "correct": correct,
            "precision": correct / predicted if predicted else None}


def build_replay(base: Simulator, final: Simulator) -> dict:
    """Side-by-side lookup feed of the normal cache and the final AI cache."""
    lookups = []
    for a, b in zip(base.lookup_log, final.lookup_log):
        lookups.append([a["t"], a["client"], a["name"], a["outcome"], a["ms"], b["outcome"], b["ms"]])
    return {"start": base.record[0], "end": base.record[1], "lookups": lookups,
            "decisions": final.decision_log, "prefetches": final.prefetch_log}


if __name__ == "__main__":
    main()
