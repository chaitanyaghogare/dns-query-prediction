# Current State

## Status

**Live app (backend + frontend) and Results & Insights page: complete.** They sit alongside the static dashboard (Phase 8).

- Start it with `python -m dnscache.app --open` → http://127.0.0.1:8000. The second tab, "Results & Insights", is the analysis page (D31).
- Next: Phase 9 (final integration, clean-up, report).

**Final system** = ML refresh (decision tree, threshold 0.5) + follow-up prefetch (p ≥ 0.7).

Plain-language explanation: `PROJECT_EXPLAINED.md` (sections 1–13).

## History

- 2026-10-05: Corrected this file (no logging or dataset existed); Phase 1 (foundation, schema, adapters, forwarder, assessment)
- 2026-10-06: Phase 2 tooling (dataset survey, synthetic generator, pcap and LANL adapters)
- 2026-10-09: Thapar prepared (primary real dataset, D20); own-trace collection dropped
- 2026-10-09: Phase 3 (simulator, baselines, oracle); `architecture.md` rewritten
- 2026-10-10: Phases 4–7 (decision datasets, predictors, ML refresh policy, follow-up prefetch); `PROJECT_EXPLAINED.md`
- 2026-10-10: Phase 8 (static dashboard, D29)
- 2026-10-10: Live app with separate backend/frontend (D30)
- 2026-10-12: Results & Insights page + workload analysis (D31); 114 tests passing

## Results & Insights page (D31)

- `frontend/insights.html` + `insights.js`, served from `GET /api/insights/{dataset}` (`src/dnscache/app/insights.py`). The live page is unchanged apart from a nav link.
- Sections:
  - A: overview, read from config and metadata only
  - B: strategy comparison (bars, hits-vs-upstream scatter, sortable table)
  - C: normal vs final (points vs relative %, per-metric direction, background fetches treated as a trade-off)
  - D: findings labelled Measured / Interpretation / Hypothesis, with a rule-based verdict
  - E: potential applications
  - F: workload analysis
  - G: limitations and prioritised future work
- Workload analysis: `python -m dnscache.app.analysis --dataset thapar --part all` → `experiments/results/insights/thapar/*.json`. Computed for thapar only; for synth the page lists the missing parts.
- Key workload findings (thapar):
  - Frequent domains (76% of lookups): 68.7% → 81.5% hits.
  - TTL ≤ 60 s gains most (+13.1 pts).
  - Domains not seen in training gain only +3.2 pts.
  - A 300-entry cache shrinks the gain to +5.5 pts.
  - The gain is stable across 3 latency seeds (+11.3–11.4 pts).
  - About 12 µs per ML decision in the Python simulator.
- Generated verdict: the objective is met in simulation, but the ML adds only +1.4 pts over the simple `refresh_if_used` rule at about the same upstream cost ("Unclear").
- Checked in headless Chromium at 1280 px and 390 px, light and dark, for both datasets: no JS errors and no horizontal overflow.

## Live app summary (D30)

**Backend** `src/dnscache/app/` (FastAPI + uvicorn):
- `engine.py`: `LiveSession` replays a trace at 0.1–1000× speed through the chosen strategy and a comparison cache in lockstep.
  - It warms up for 1 h first (not shown).
  - It publishes a tick every 0.25 s: metrics, the newest lookups with both caches' outcomes, ML decisions with confidence, follow-up fetches, and a rolling 5-minute hit rate.
  - It supports pause, resume, speed change and stop. At most 4 sessions run at once.
- `server.py`: REST + Server-Sent Events endpoints:
  - `/api/health`, `/api/datasets`, `/api/strategies`, `/api/results/{dataset}`, `/api/insights/{dataset}`
  - `/api/sessions`: POST to create; GET a snapshot; `/control`, `/stream` (SSE), `/series`; DELETE
  - CORS origins and datasets are configured in `configs/app.yaml`.
- `Simulator` now has an incremental API (`begin` / `step` / `finish`); `run()` is exactly these three. Live results equal batch results, and a test enforces this.

**Frontend** `frontend/` (`index.html`, `insights.html`, `styles.css`, `app.js`, `insights.js`): static, no build step, no external scripts, talks only to the API.
- Served by the backend at `/`, or hosted separately with `?api=…`.
- Controls: dataset, strategy, comparison strategy, confidence sliders, start hour, speed, start/pause/stop.
- Live KPIs against the comparison cache.
- Live rolling hit-rate chart and answer-mix bars.
- Lookup feed showing both caches' outcomes, with rows where they differ highlighted.
- Model decisions with confidence bars, follow-up fetches, and the pre-computed test-period results table.

**Checks:**
- 114 tests passing.
- Run end-to-end on the user's laptop with the Thapar data.
- Driven in headless Chromium: no JS errors and no horizontal scroll at phone width.

**Dependencies added:** fastapi, uvicorn (runtime), httpx (tests only).

## Phase 8 dashboard (D29)

`dashboard/index.html` is an offline single page with:
- KPIs, strategy comparison and trade-off chart
- hour-by-hour view and answer mix
- a 20-minute replay
- a confidence check, most-refreshed domains and follow-up pairs

Build it with `python -m dnscache.dashboard.build --config configs/dashboard/thapar.yaml`.

## Headline results (Thapar test window, Sun 21:54 → Mon 07:54)

| Policy | Hit rate | Mean latency | P95 latency | Upstream queries per demand | Prefetch precision |
|---|---|---|---|---|---|
| ttl_only (conventional) | 58.5% | 323 ms | 1790 ms | 0.400 | — |
| unbound_prefetch | 61.2% | 301 ms | 1733 ms | 0.412 | 82% |
| refresh_if_used heuristic | 68.5% | 244 ms | 1562 ms | 0.484 | 65% |
| ML refresh (tree @0.5) | 69.4% | 238 ms | 1549 ms | 0.478 | 70% |
| **Final: ML refresh + follow-up p ≥ 0.7** | **69.9%** | **234 ms** | **1539 ms** | **0.484** | **69%** |
| oracle (future knowledge) | 72.4% | 214 ms | 1483 ms | 0.426 | 99.5% |

Details: `docs/decisions.md` D24–D31, `experiments/results/phase6/`, `experiments/results/phase7/`, `experiments/results/insights/`.

## Implementation Plan

1–8: done; live app and Results & Insights page done.

9. **Final integration (next):**
   - clean-up of stale files, configs that were never run, `data/interim/appcheck.tgz` and `data/interim/insights_check/`
   - full test run
   - a reproduce-everything script
   - final README review
   - report / presentation material

## Known Issues / Limitations

- **Thapar:** 32 hours only; 10% client sample; TTL estimate is a lower bound.
- **Test window composition:** the test window is mostly night plus Monday morning.
- **Validation → test transfer:** operating points move slightly between windows.
- **Policy shift (D27):** online precision is about 5–7 points below offline.
- **Sequence model:** only learns 1–30 s follow-ups seen in training.
- **Synthetic data:** generator uncalibrated, with no delayed follow-up structure (D17, D28). Workload analysis not computed for synth.
- **Live app miss latencies:** some "expired" misses show < 1 ms. The upstream latency samples include cases where the campus back-end resolver answered from its own cache. This is real data, not a bug.
- **Live app event list:** at high speeds the lookup list is sampled (max 150 events per tick). All lookups are still counted in the metrics.
- **Stale files:**
  - `experiments/results/ml_*/tree_rules.txt` is superseded.
  - the synthetic DAgger configs are marked NOT RUN.
  - `data/interim/appcheck.tgz` and `data/interim/insights_check/` are copies used for browser tests and can be deleted.

## Last Updated

2026-10-12
