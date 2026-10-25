# F1_Team28 — Testing results

AI-assisted DNS Cache Optimisation and Query Prediction (slot F1, team 28).

Run on 2026-10-25 from a fresh clone and a fresh virtual environment:

```
pip install -r requirements.txt
python -m pytest -v
python run_all.py
python demo.py run --offline
```

Environment: Python 3.13, numpy 2.5.4, scikit-learn 1.9.1, pytest 9.1.1.
The project needs Python 3.10 or newer.

## 1. Unit and integration tests: 41 passed, 0 failed

| Test file | What it checks |
|---|---|
| `test_cache_sim.py` | TTL expiry, LRU eviction, in-flight lookups, miss types, metrics only inside the evaluation window, every policy |
| `test_data.py` | trace schema, CSV round trip, seeded generator, quality check, canonical, tshark and pcap adapters |
| `test_models.py` | features use only the past, tree predictor matches scikit-learn, model comparison, follow-up (transition probability) model |
| `test_forwarder.py` | DNS wire format, TTL rewrite, the live caching forwarder (caching, logging, prefetch, online follow-ups) |
| `test_demo.py` | live demo: hit/miss/TTL, AI prefetch, upstream client, end-to-end run, domain-list input, `serve` mode |
| `test_pipeline.py` | follow-ups improve hit rate on the synthetic trace, every policy sees the same network, fractional windows |

```
tests/test_cache_sim.py::test_lru_eviction_order PASSED                  [  2%]
tests/test_cache_sim.py::test_hit_within_ttl_and_miss_after PASSED       [  4%]
tests/test_cache_sim.py::test_inflight_coalescing PASSED                 [  7%]
tests/test_cache_sim.py::test_eviction_miss_type PASSED                  [  9%]
tests/test_cache_sim.py::test_refresh_if_used_keeps_entry_alive PASSED   [ 12%]
tests/test_cache_sim.py::test_unused_entry_is_not_refreshed_by_rule PASSED [ 14%]
tests/test_cache_sim.py::test_oracle_refreshes_only_when_needed PASSED   [ 17%]
tests/test_cache_sim.py::test_unbound_prefetch_on_late_hit PASSED        [ 19%]
tests/test_cache_sim.py::test_ml_policy_uses_predictor_and_logs PASSED   [ 21%]
tests/test_cache_sim.py::test_followup_prefetch_turns_first_miss_into_hit PASSED [ 24%]
tests/test_cache_sim.py::test_window_only_counts_inside PASSED           [ 26%]
tests/test_cache_sim.py::test_summary_percentiles PASSED                 [ 29%]
tests/test_data.py::test_cache_key_normalises PASSED                     [ 31%]
tests/test_data.py::test_trace_roundtrip PASSED                          [ 34%]
tests/test_data.py::test_generator_is_deterministic PASSED               [ 36%]
tests/test_data.py::test_generator_seed_changes_trace PASSED             [ 39%]
tests/test_data.py::test_assess_flags_short_trace PASSED                 [ 41%]
tests/test_data.py::test_tshark_adapter PASSED                           [ 43%]
tests/test_data.py::test_canonical_adapter_keeps_relative_times PASSED   [ 46%]
tests/test_data.py::test_pcap_adapter_reads_queries_and_ttls PASSED      [ 48%]
tests/test_data.py::test_pcap_client_sample_is_deterministic PASSED      [ 51%]
tests/test_demo.py::test_livecache_hit_miss_and_ttl PASSED               [ 53%]
tests/test_demo.py::test_ai_cache_prefetches_predicted_domain PASSED     [ 56%]
tests/test_demo.py::test_real_upstream_client_against_fake_server PASSED [ 58%]
tests/test_demo.py::test_demo_run_end_to_end PASSED                      [ 60%]
tests/test_demo.py::test_demo_domain_list_input PASSED                   [ 63%]
tests/test_demo.py::test_demo_serve_answers_nslookup_style_queries PASSED [ 65%]
tests/test_forwarder.py::test_parse_and_build_roundtrip PASSED           [ 68%]
tests/test_forwarder.py::test_ttl_rewrite PASSED                         [ 70%]
tests/test_forwarder.py::test_forwarder_caches_and_logs PASSED           [ 73%]
tests/test_forwarder.py::test_forwarder_prefetch_marks_hit PASSED        [ 75%]
tests/test_forwarder.py::test_online_followups PASSED                    [ 78%]
tests/test_models.py::test_tree_predictor_matches_sklearn PASSED         [ 80%]
tests/test_models.py::test_train_and_evaluate_reports_all_models PASSED  [ 82%]
tests/test_models.py::test_features_use_only_the_past PASSED             [ 85%]
tests/test_models.py::test_future_index PASSED                           [ 87%]
tests/test_models.py::test_sequence_model_probabilities PASSED           [ 90%]
tests/test_models.py::test_sequence_model_roundtrip PASSED               [ 92%]
tests/test_pipeline.py::test_followups_improve_hit_rate_on_synthetic_trace PASSED [ 95%]
tests/test_pipeline.py::test_same_network_for_every_policy PASSED        [ 97%]
tests/test_pipeline.py::test_fractional_windows_cover_the_whole_trace PASSED [100%]
============================== 41 passed in 3.20s ==============================
```

## 2. Experiment results (trace replay, `python run_all.py`)

Synthetic trace: 82,818 lookups, 40 clients, 48 h. Trained on hours 0–24,
threshold chosen on 24–32, measured on 32–48 (36,000 lookups the models never
saw). The run is seeded, so these numbers are reproduced exactly. Full
numbers: [`experiments/results/synth/comparison.csv`](../../experiments/results/synth/comparison.csv).

| Strategy | Hit rate | Mean latency | P95 latency | Upstream per lookup | Prefetch precision | Unnecessary prefetches |
|---|---|---|---|---|---|---|
| Normal TTL cache | 64.1% | 40.5 ms | 127 ms | 0.358 | — | 0 |
| Unbound-style prefetch | 66.9% | 37.2 ms | 120 ms | 0.373 | 81% | 288 |
| Refresh-if-used rule | 75.3% | 28.1 ms | 95 ms | 0.479 | 60% | 3363 |
| TTL cache + follow-up prefetch | 79.1% | 24.3 ms | 82 ms | 0.479 | 69% | 3025 |
| ML refresh (decision tree) | 69.6% | 34.1 ms | 113 ms | 0.385 | 83% | 506 |
| **Final AI cache** | **83.9%** | **19.5 ms** | **76 ms** | **0.504** | **72%** | **3518** |
| Oracle (knows the future) | 78.7% | 24.4 ms | 86 ms | 0.392 | 98% | 160 |

Model metrics (validation window, decision tree): precision 0.66, recall 0.71,
F1 0.68, ROC-AUC 0.79. Follow-up predictions with p ≥ 0.7 came true 83% of the
time on the test window.

## 3. Live demo (`python demo.py run`)

113 lookups from a 10-minute browsing session, answered by both caches.
This run used `--offline` (simulated upstream); a run on a real network gives
different latencies.

| Metric | Normal cache | AI cache |
|---|---|---|
| Hit rate | 56.6% | 73.5% |
| Average lookup time | 68.3 ms | 34.5 ms |
| 95th percentile lookup time | 386.5 ms | 107.7 ms |
| Upstream queries in total | 49 | 49 |
| Prefetches used / unnecessary | — | 20 / 0 |
| Prediction accuracy | — | 52 of 64 (81.2%) |

Screenshots: [`docs/screenshots/`](../screenshots/).
