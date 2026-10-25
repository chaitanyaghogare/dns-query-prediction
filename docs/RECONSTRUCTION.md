# What was rebuilt, and how faithfully

The original code is on another laptop. This version was rebuilt on
2026-10-22 from the notes in `knowledge/` (current_state.md, architecture.md,
ML.md, project.md, agents.md). Nothing from the original code was available,
so **no file here is a copy**; everything was rewritten to match the notes.

## Rebuilt to match the notes

* Two-path architecture: plain cache on the fast path, ML only in background
  decisions taken 2 s before expiry (architecture.md).
* Discrete-event trace replay with TTL expiry, LRU capacity, in-flight
  lookups, miss breakdown (first-time / expired / evicted) and metrics only
  inside the evaluation window.
* Baselines `ttl_only`, `unbound_prefetch`, `refresh_if_used`, `oracle_refresh`,
  and `ExpiryRefreshPolicy` as the slot the ML predictor plugs into.
* Decision-time features (frequency, time since last query, 1/5/30-minute
  counts, hour, TTL, cache status...) from ML.md.
* Decision tree as the refresh model, compared offline with simpler models.
* Final system = ML refresh + follow-up prefetch with p >= 0.7 (current_state.md).
* Chronological train / validation / test split, headline numbers from test only.
* Metrics: hit/miss/in-flight rates, mean/P50/P95/P99 latency, upstream
  queries (demand + prefetch), prefetch precision, wasted prefetches, occupancy.
* DAgger-style retraining against policy shift (the notes mention D27 and DAgger configs).
* Static offline dashboard with KPIs, comparison, trade-off chart, hour view,
  answer mix and a 20-minute replay; Results & Insights section with findings
  labelled Measured / Interpretation / Hypothesis, limitations and future work.
* Logging pass-through forwarder (`collect/forwarder.py`).
* Adapters for canonical CSV and tshark exports.

## Guessed or simplified (the notes did not say how)

* **Dataset.** The original used the Thapar campus trace, which is not
  available here. The default dataset is a new seeded synthetic workload
  (48 h, 40 clients, ~83k lookups). Results are therefore **different from
  the Thapar numbers** in current_state.md (e.g. 58.5% → 69.9% hit rate there,
  64.1% → 83.9% here). Expect real data to show a smaller gain.
* **Upstream latency model.** Log-normal per-domain latency (median ~45 ms)
  with a 6% slow tail; the original sampled latencies from Thapar.
* **Follow-up model details**: 30 s window, per client, min support 5.
* **Label** for the refresh model: the name is looked up again within one TTL
  after expiry.
* **Threshold choice**: lowest threshold whose validation prefetch precision
  is >= 70%. It picked 0.8 here (the original used 0.5 on Thapar).
* **Tree settings**: max depth 5, min 50 samples per leaf.
* Training rows come from replaying with the refresh-if-used rule, then two
  rounds under the ML policy itself.

## Not recreated

* The FastAPI + Server-Sent Events live app (D30). The dashboard's "Live
  replay" tab plays back pre-computed lookups instead, and
  `python -m dnscache.app` serves it with the standard library only. No
  fastapi/uvicorn dependency.
* The pcap and LANL adapters (tshark export covers pcap files).
* Workload analysis by TTL band / domain frequency (section F of the old page).
* `docs/decisions.md` D1–D31 and `PROJECT_EXPLAINED.md`; their key points are
  folded into README.md and this file.
* 114 tests → 32 tests here, covering cache, simulator, policies, models,
  data, forwarder and an end-to-end check.

When you get the laptop back, compare `src/dnscache/sim/simulator.py` and the
Thapar results with the originals before mixing the two versions.
