# System Architecture

(Rewritten 2026-10-09. The original put the model in front of every lookup; see
`docs/decisions.md` D1. Design rationale for each part is in D1–D23.)

## Two paths

**Fast path: every query, no ML involved**

DNS query → cache lookup → hit: answer from the cache
                         → miss: ask the upstream server, answer, insert with the upstream TTL

**Slow path: background decisions, ML lives here**

Decision point (an entry is 2 s from expiry, or a hit arrives) → policy → predictor (Phase 5) → refresh the entry in the background, or let it expire

The ML component sits behind the policy interface, so it can be replaced
without touching the cache.

## Components (code in `src/dnscache/`)

| Component | Module | Role |
|-----------|--------|------|
| Query logging / collection | `collect/forwarder.py` | Logging pass-through forwarder for own traces (optional; not used for the final data) |
| Dataset import | `data/adapters/` (`pcap`, `tshark`, `canonical`, `lanl`) | Convert any source into the canonical trace schema |
| Schema, storage, quality check | `data/schema.py`, `data/io.py`, `data/assess.py` | Canonical schema v1, manifests, checks against the requirements |
| Upstream environment | `data/upstream.py`, `sim/environment.py` | What upstream returns on a miss or prefetch: TTL per key, plus latency |
| Synthetic data | `synth/` | Seeded generator for controlled experiments |
| Cache + simulator | `sim/simulator.py` | Discrete-event replay: TTL expiry, LRU capacity, queries in flight, miss types, metrics per evaluation window |
| Cache policies | `sim/policies.py` | `ttl_only`, `unbound_prefetch`, `refresh_if_used`, `oracle_refresh`; `ExpiryRefreshPolicy` is the interface for the ML policy |
| Experiment runner | `sim/run.py`, `configs/experiments/*.yaml` | Run policy × capacity grids, write results |
| Feature extraction + prediction | *Phase 4–5* | Features available at decision time only → predicted probability of a re-query within the refreshed lifetime |
| ML-assisted policy | *Phase 6* | `ExpiryRefreshPolicy(decide = predictor ≥ threshold)` |
| Dashboard | *Phase 8* | Queries, hits/misses, predictions with confidence, prefetches, comparison |

## Evaluation

- Main evaluation is trace replay, not a live resolver (D6).
- Data: Thapar campus trace (real, primary) and synthetic traces (controlled).
- Metrics:
  - hit, miss and in-flight rates
  - miss breakdown (first-time / expired / evicted)
  - mean, P50, P95 and P99 latency
  - upstream queries (demand + prefetch)
  - prefetch precision and wasted prefetches
  - cache occupancy
- Baselines: plain TTL cache, TTL+LRU, Unbound-style prefetch, refresh-if-used heuristic, and the oracle upper bound.
- Headline numbers come from the chronological test window (D23).
