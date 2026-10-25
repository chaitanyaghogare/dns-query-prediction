# AI-assisted DNS Cache Optimisation and Query Prediction

Computer Networks course project. A local DNS cache that logs every lookup,
learns which domains are likely to be asked for next, and prefetches them
(respecting TTL) so the answer is already in the cache when it is needed.
It is compared against a normal TTL cache and several standard baselines.

> This is a **rebuild** of the original project from its notes (the original
> code is on another laptop). See [docs/RECONSTRUCTION.md](docs/RECONSTRUCTION.md)
> for what was recreated faithfully and what had to be guessed.

## Quick start (Windows)

```bat
cd C:\Users\Hanuman\Documents\Chaitanya\CN
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run_all.py
```

`run_all.py` takes about a minute. It generates the dataset, trains the
models, runs every strategy and rebuilds the dashboard. Then open
`dashboard\index.html` in a browser (double-click works), or:

```bat
set PYTHONPATH=src
python -m dnscache.app --open
python -m pytest
```

(On Linux/macOS use `python3 -m venv .venv`, `source .venv/bin/activate` and
`export PYTHONPATH=src`. `pip install -e .` also works and removes the need
for `PYTHONPATH`.)

## Live demo (real DNS queries)

`python demo.py run` answers a defined input (a 10-minute browsing session of
real websites) with a normal cache and the AI cache side by side, using real
DNS queries, and writes defined outputs (`demo/output/report.html`,
`lookups.csv`, `predictions.csv`, `summary.json`). There are also
`interactive` (type domains) and `serve` (a real DNS server for `nslookup`)
modes. Step-by-step presentation guide: [DEMO.md](DEMO.md).

## How it works

Two paths, so the ML never slows down an ordinary lookup:

* **Fast path (every lookup, no ML).** Hit: answer from the cache.
  Miss: ask upstream, answer, store with the upstream TTL. Lookups that
  arrive while the answer is on its way wait for it (\"in flight\").
* **Slow path (background, where prediction lives).**
  1. **Follow-up prediction (transition probabilities).** After a lookup of
     domain A, every domain B with P(B within 30 s | A) ≥ 0.7 is prefetched if it
     is not already cached. P is just `count(A then B) / count(A)` from the
     training window, so each prediction can be explained with two numbers.
  2. **Refresh prediction (decision tree).** 2 s before an entry expires, a
     depth-5 decision tree predicts whether it will be asked for again during
     the next TTL. If the confidence is ≥ the threshold, the entry is renewed
     in the background; otherwise it expires as normal.

Prefetched records get their real upstream TTL, so nothing is served past its
TTL.

## Evaluation

Main evaluation is **trace replay** in a discrete-event simulator, so every
strategy sees exactly the same lookups and the same network. The 48 h trace is
split by time: train on hours 0–24 (Monday), choose the threshold on hours 24–32,
report on hours 32–48 (Tuesday daytime), which the models never saw.

Results on the test window (36,000 lookups, unlimited cache):

| Strategy | Hit rate | Mean latency | P95 latency | Upstream per lookup | Prefetch precision | Unnecessary prefetches |
|---|---|---|---|---|---|---|
| Normal TTL cache | 64.1% | 40.5 ms | 127 ms | 0.358 | — | 0 |
| Unbound-style prefetch | 66.9% | 37.2 ms | 120 ms | 0.373 | 81% | 288 |
| Refresh-if-used rule | 75.3% | 28.1 ms | 95 ms | 0.479 | 60% | 3363 |
| TTL cache + follow-up prefetch | 79.1% | 24.3 ms | 82 ms | 0.479 | 69% | 3025 |
| ML refresh (decision tree) | 69.6% | 34.1 ms | 113 ms | 0.385 | 83% | 506 |
| **Final: ML refresh (p>=0.8) + follow-up (p>=0.7)** | **83.9%** | **19.5 ms** | **76 ms** | **0.504** | **72%** | **3518** |
| Oracle (knows the future) | 78.7% | 24.4 ms | 86 ms | 0.392 | 98% | 160 |

* **Hit rate 64.1% → 83.9%**, mean latency **40.5 → 19.5 ms**, P95 **127 → 76 ms**.
* Cost: upstream queries rise from 0.358 to 0.504 per lookup; 72% of prefetches
  were used, 28% were unnecessary.
* Against the simple refresh-if-used rule: +8.7 points of hit rate for 5% more upstream traffic.
* The oracle only knows which *refreshes* will pay off; it cannot help the first
  lookup of a domain, which follow-up prediction can. That is why the final system beats it.
* With a 40-entry LRU cache the ranking is the same (see the dashboard toggle).

Offline model metrics (validation window, tree): precision 0.66, recall 0.71,
F1 0.68, ROC-AUC 0.79. Follow-up predictions with p ≥ 0.7 came true 83% of the
time on the test window. As the original notes say, model accuracy is not the
goal: the cache numbers above are.

## Dashboard

`dashboard/index.html` is one offline file with two tabs:

* **Live replay**: 20 minutes of the test window (Tue 19:00) played back at
  1–60× speed. Shows each lookup with the outcome in the normal and AI cache,
  live hit rate/latency/upstream counters, the tree's decisions with
  confidence, and the prefetched domains with their probabilities.
* **Results & insights**: comparison table and charts for all strategies,
  hit rate vs network cost, hour-by-hour hit rate, findings, model metrics,
  feature importance, the full decision-tree rules and the strongest follow-up pairs.

## Live DNS forwarder (optional demo)

A real caching forwarder that logs to CSV and learns follow-ups online:

```bat
python -m dnscache.collect.forwarder --port 5300 --predict
nslookup -port=5300 www.wikipedia.org 127.0.0.1
python -m dnscache.collect.query www.wikipedia.org --port 5300
```

Log: `data/live/queries.csv` (ts, client, domain, type, hit/miss, latency, TTL,
prefetched). Needs UDP access to 8.8.8.8 (change with `--upstream`).

## Using a real capture

Export DNS queries from Wireshark/tshark (command in
`src/dnscache/data/adapters/tshark.py`), or any CSV with `ts,client,qname`
columns, then create a config such as:

```json
{"name": "mytrace", "trace_csv": "data/mytrace/queries.csv",
 "windows_h": {"train": [0, 12], "validation": [12, 16], "test": [16, 24]},
 "replay_start_h": 18}
```

and run `python run_all.py --config configs/mytrace.json`. Without a TTL file,
every domain gets the default TTL of 300 s.

## Project layout

| Path | What |
|---|---|
| `src/dnscache/data/` | trace schema, CSV I/O, quality check, adapters (canonical CSV, tshark) |
| `src/dnscache/synth/generator.py` | seeded synthetic workload (apps + follow-ups, revisits, polling, long tail, day/night) |
| `src/dnscache/sim/` | cache (TTL + LRU), upstream model, discrete-event simulator, policies |
| `src/dnscache/predict/` | decision-time features, refresh models (tree / naive Bayes / logistic regression), follow-up model |
| `demo.py`, `src/dnscache/demo/` | live demo: real resolver with normal vs AI cache, inputs in `demo/input/`, outputs in `demo/output/` |
| `src/dnscache/collect/` | live caching forwarder, DNS wire-format helpers, test client |
| `src/dnscache/dashboard/` | dashboard builder + HTML template |
| `src/dnscache/run.py` | the whole experiment pipeline |
| `configs/default.json` | all settings |
| `models/synth/` | trained tree (`refresh_tree.pkl`, `tree_rules.txt`) and `sequence.json` |
| `experiments/results/synth/` | `comparison.csv`, `summary.json` |
| `tests/` | 38 pytest tests |
| `knowledge/` | the original project notes |
