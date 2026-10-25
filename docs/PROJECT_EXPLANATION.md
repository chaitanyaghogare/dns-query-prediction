# AI-assisted DNS Cache Optimisation and Query Prediction: project explanation

Computer Networks course project. This document explains the project end to end
by answering eight questions:

1. [What is the problem statement?](#1-problem-statement)
2. [What solution did we find?](#2-the-solution)
3. [How does it work?](#3-how-it-works)
4. [What are the inputs and outputs?](#4-inputs-and-outputs)
5. [What are the limitations?](#5-limitations)
6. [How is AI used?](#6-how-ai-is-used)
7. [What are the key results?](#7-key-results)
8. [What is novel?](#8-what-is-novel)

> **A note on the numbers.** The code in this folder is a rebuild of the
> original project (the original code is on another laptop). The large-scale
> evaluation here runs on a **seeded synthetic trace** (48 hours, 40 clients,
> about 83,000 lookups), not on the Thapar campus trace the original used, so
> its numbers differ from the original Thapar results. Both sets are given in
> section 7 and labelled. See [RECONSTRUCTION.md](RECONSTRUCTION.md) for what was rebuilt
> faithfully and what was guessed.

---

## 1. Problem statement

### Background: why DNS lookups matter

Before a browser can open `github.com`, it must translate the name into an IP
address using DNS. A single web page usually needs several names: the main
site plus its CDN, image, font, script and API hosts (for example
`github.com` is followed within a second by `github.githubassets.com` and
`avatars.githubusercontent.com`). Each name that is not already known costs a
round trip to a DNS server, typically 10 to 150 ms and sometimes much more.
Until DNS answers, the connection cannot even start, so DNS latency adds
directly to page-load time.

### How a normal DNS cache works

Resolvers keep a cache. Every DNS answer carries a **TTL** (time to live, in
seconds). The cache keeps the answer for that long and then throws it away.

* If a name is requested while its answer is cached: **cache hit**, answered in
  well under a millisecond.
* Otherwise: **cache miss**, the resolver must ask the upstream server and the
  user waits.

### What is wrong with that

A normal TTL cache is purely **reactive**. It only knows what was asked in the
past, and it makes two kinds of avoidable misses:

1. **Expiry misses.** A popular entry expires and the very next lookup misses,
   even though it was obvious the name would be asked for again. On our test
   window, 12,160 of the normal cache's 12,893 misses (94%) were of this kind.
2. **Predictable first-lookup misses.** After `www.youtube.com` the browser
   almost always asks for `i.ytimg.com` a moment later. The cache could have
   fetched it in advance but does not, because it never looks at *patterns*
   between lookups.

The simple fix of "refresh everything before it expires" removes expiry misses
but floods the upstream server with queries for names nobody uses again.

### Problem statement

> Design a local DNS cache that learns from historical query patterns which
> domains are likely to be requested next, and prefetches or refreshes those
> records in advance (respecting their TTL), so that more lookups are answered
> from the cache and lookup latency drops, **without generating excessive
> unnecessary DNS traffic**. Compare it against a standard TTL cache on
> cache-hit ratio, lookup latency, prediction accuracy, upstream query count
> and unnecessary prefetches.

Two constraints shaped the whole design:

* The AI must stay **basic and explainable** (transition probabilities, a
  decision tree, Naive Bayes; no deep learning).
* **Prediction accuracy is not the goal; cache performance is.** A model that
  is 95% accurate but does not make the cache faster has failed.

---

## 2. The solution

We built an **AI-assisted DNS cache** that adds two predictions on top of a
normal TTL cache:

| Prediction | Question it answers | Model | What it does when confident |
|---|---|---|---|
| **Follow-up prediction** | "This client just looked up A. Which domains will it look up in the next 30 seconds?" | Transition probabilities, P(B follows A) | **Prefetches** B if it is not cached (fixes predictable first-lookup misses) |
| **Refresh prediction** | "This entry expires in 2 seconds. Will anyone ask for it again during its next TTL?" | Depth-5 decision tree | **Refreshes** the entry in the background (fixes expiry misses) |

Each prediction comes with a confidence score, and the cache acts only when
the confidence is above a threshold (0.7 for follow-ups, 0.8 for refreshes,
chosen on validation data). Everything else behaves exactly like a normal
cache. Prefetched and refreshed records get their **real upstream TTL**, so
nothing is ever served past its TTL.

Around this core the project delivers:

* a **trace-replay simulator** that runs the normal cache, the AI cache and five
  other strategies on exactly the same lookups, for a fair comparison;
* a **live demo** that sends real DNS queries and answers every lookup with a
  normal cache and the AI cache side by side (`python demo.py run`);
* a real **caching DNS forwarder/server** that `nslookup` can query;
* an offline **dashboard** (`dashboard/index.html`) and an HTML **report**
  (`demo/output/report.html`) showing queries, hits/misses, predictions with
  confidence, prefetched domains and the comparison.

---

## 3. How it works

### 3.1 Architecture: a fast path and a slow path

The central design decision is that **the ML never sits in front of a lookup**.
Putting a model in the lookup path would add latency to every query, which is
the opposite of the goal. So the system has two paths:

```
                         FAST PATH (every lookup, no ML)
  DNS query ──► cache lookup ──► HIT  ──► answer from cache (~0.05 ms)
                    │
                    └──────────► MISS ──► ask upstream DNS ──► answer user
                                                         └──► store with upstream TTL

                         SLOW PATH (background, where the AI lives)
  after each lookup of A ──► follow-up model: P(B | A) ≥ 0.7 ? ──► prefetch B
  2 s before an entry expires ──► decision tree: P(reuse) ≥ 0.8 ? ──► refresh it
                                                              else ──► let it expire
```

Lookups that arrive while a prefetch is already on its way simply wait for
that answer (counted as "in flight" / "WAIT"), instead of sending a duplicate
query.

The prediction logic plugs into the cache through a **policy interface**
(`src/dnscache/sim/policies.py`), so the same cache can run with no prediction
(normal cache), with a simple rule, with the ML model, or with an "oracle" that
knows the future, without changing the cache code.

### 3.2 Step 1: logging lookups

Every lookup is recorded with: timestamp, client, domain (and record type),
cache hit or miss, lookup latency, TTL, and whether the answer came from a
prefetch. The live forwarder writes this to `data/live/queries.csv`; the demo
writes it to `demo/output/lookups.csv`. Historical logs are the training data.

### 3.3 Step 2: learning follow-up patterns (transition probabilities)

From the history, for every lookup of domain A by a client, we record which
other domains B the **same client** looked up within the next **30 seconds**.
Then:

```
P(B follows A) = count(A was followed by B within 30 s) / count(A)
```

Pairs seen fewer than 5 times are ignored (minimum support), and each domain
keeps at most its 8 strongest followers. This is a first-order Markov /
association-rule model (`src/dnscache/predict/sequence.py`). Example learned
rules:

* after `www.youtube.com` → `i.ytimg.com`, p = 1.00
* after `github.com` → `github.githubassets.com`, p = 0.90

At run time, after each lookup of A, every follower B with P ≥ 0.7 that is not
already cached is prefetched in the background. In the live demo the model also
keeps learning from new lookups as they arrive.

### 3.4 Step 3: deciding which entries to refresh (decision tree)

Two seconds before a cache entry expires, the system computes features that are
**known at that moment** (no peeking into the future,
`src/dnscache/predict/features.py`):

| Feature | Meaning |
|---|---|
| `ttl` | TTL of the record |
| `hits_this_life` | how many lookups this cached copy answered |
| `since_last_q`, `since_last_over_ttl` | time since the name was last asked for (seconds, and as a fraction of the TTL) |
| `q_1m`, `q_5m`, `q_30m` | lookups of the name in the last 1, 5 and 30 minutes |
| `clients_30m` | distinct clients that asked for it in the last 30 minutes |
| `total_q`, `mean_gap` | lookups so far and average gap between them |
| `hour` | hour of the day |
| `was_refresh`, `unused_refreshes` | whether this copy came from a refresh, and how many refreshes in a row nobody used |

The decision tree outputs the probability that the name will be asked for again
during the next TTL. If it is at least 0.8, the entry is renewed in the
background; otherwise it expires normally.

**Training labels** come from history: "after this expiry, was the name really
looked up again within one TTL?" Training data is generated by replaying the
training window with a simple rule, then retrained twice under the ML policy
itself (a DAgger-style loop), because once the model starts refreshing entries
the situations it sees change.

### 3.5 Step 4: fair evaluation by trace replay

The main evaluation is a **discrete-event simulator** (`src/dnscache/sim/`)
that replays the same trace through every strategy, modelling TTL expiry,
optional LRU capacity, in-flight lookups and upstream latency (log-normal per
domain, median about 45 ms, with a 6% slow tail).

The 48-hour trace is split **by time**, never randomly:

| Window | Hours | Used for |
|---|---|---|
| Train | 0 to 24 (Monday) | learning follow-up pairs and training the tree |
| Validation | 24 to 32 | choosing the confidence threshold |
| Test | 32 to 48 (Tuesday daytime) | all reported numbers; the models never saw it |

The threshold was chosen as the lowest one whose prefetch precision on
validation is at least 70%. That gave 0.8.

Strategies compared:

| Strategy | What it does |
|---|---|
| Normal TTL cache | the standard baseline, no prediction |
| Unbound-style prefetch | refreshes an entry if it is hit in the last 10% of its TTL (what the Unbound resolver does) |
| Refresh-if-used rule | refreshes any entry that was used at least once during its life |
| TTL cache + follow-up prefetch | transition-probability prefetch only |
| ML refresh (decision tree) | decision-tree refresh only |
| **Final AI cache** | decision-tree refresh (p ≥ 0.8) + follow-up prefetch (p ≥ 0.7) |
| Oracle | refreshes exactly the entries that will be reused (knows the future; an upper bound for refreshing) |

### 3.6 Step 5: live demo with real DNS

`python demo.py run` (`src/dnscache/demo/`) learns the follow-up model from
`demo/input/history.csv`, then plays a 10-minute browsing session of real
websites. Every lookup goes to **two real caches** (`LiveCache("normal")` and
`LiveCache("ai")`) that send **real DNS queries** to a real upstream server
(this PC's DNS server, or 8.8.8.8 / 1.1.1.1). Both caches see exactly the same
lookups, so any difference comes only from prediction. Other modes:

* `python demo.py interactive`: type domains by hand.
* `python demo.py serve`: run as a real DNS server on port 5300 and query it
  with `nslookup -port=5300 stackoverflow.com 127.0.0.1`.
* `--offline`: simulate the upstream if the network blocks DNS.

---

## 4. Inputs and outputs

### 4.1 Live demo (`python demo.py run`)

| | File | Contents |
|---|---|---|
| **Input 1** | `demo/input/history.csv` | 1,907 past lookups (`time_s, client, domain`) from 6 lab PCs over 4 hours. The AI learns from this. |
| **Input 2** | `demo/input/session.csv` | 113 lookups from a 10-minute browsing session of real sites (YouTube, GitHub, Stack Overflow, Wikipedia, Amazon, LinkedIn...). These are answered live. |
| Input 2 (alternatives) | any `.txt` list of domains (e.g. `demo/input/my_domains.txt`), typed domains (`interactive`), or `nslookup` queries (`serve`) | |
| **Output 1** | console | one line per lookup: normal cache HIT/MISS and time, AI cache HIT/MISS and time, plus each prediction with its confidence; then a comparison table |
| **Output 2** | `demo/output/lookups.csv` | every lookup: time, client, domain, HIT/MISS and ms for both caches, TTL, IP address |
| **Output 3** | `demo/output/predictions.csv` | every prediction: after which domain, predicted domain, confidence, prefetched or not, whether it was used, and why (the counts behind it) |
| **Output 4** | `demo/output/summary.json` | all the numbers |
| **Output 5** | `demo/output/report.html` | one page with all of the above |

Example of console output:

```
github.com               normal: MISS  30 ms   | AI: MISS  30 ms
  predicts github.githubassets.com next (confidence 90%) -> prefetched
github.githubassets.com  normal: MISS 126 ms   | AI: HIT 0.05 ms  <- answered from a prefetch
```

### 4.2 Simulation pipeline (`python run_all.py`)

| | What |
|---|---|
| **Input** | `configs/default.json` (settings: windows, thresholds, latency model) and a DNS query trace. By default a seeded synthetic trace is generated; a real capture can be used instead (Wireshark/tshark export, or any CSV with `ts, client, qname` columns). |
| **Output** | `models/synth/refresh_tree.pkl` and `tree_rules.txt` (the trained tree and its readable rules), `models/synth/sequence.json` (the follow-up probabilities), `experiments/results/synth/comparison.csv` and `summary.json` (metrics for every strategy), and `dashboard/index.html` |

The dashboard has two tabs. **Live replay** plays back 20 minutes of the test
window at 1 to 60× speed, showing each lookup's outcome in the normal and AI
cache, live hit-rate/latency/upstream counters, the tree's decisions with
confidence, and prefetched domains with their probabilities. **Results &
insights** shows the comparison table and charts, hit rate versus network cost,
hour-by-hour hit rate, model metrics, feature importance, the full tree rules
and the strongest follow-up pairs.

### 4.3 Live forwarder (`python -m dnscache.collect.forwarder --port 5300 --predict`)

Input: real DNS queries from `nslookup` or any client. Output: real DNS answers,
plus a log at `data/live/queries.csv` (ts, client, domain, type, hit/miss,
latency, TTL, prefetched).

---

## 5. Limitations

**Data**

* The large-scale results in this rebuild come from a **synthetic** trace. The
  generator is plausible (apps with follow-up domains, revisits, background
  polling, a long tail, day/night cycle) but not calibrated against real
  traffic, and it has strong, clean follow-up structure. Real data should show a
  **smaller gain**, and the original Thapar results confirm that (section 7.3).
* The original Thapar trace covered only 32 hours and a 10% sample of clients,
  and its TTLs were estimated (a lower bound).
* The demo's `history.csv` and `session.csv` are generated from a list of real
  websites and their typical sub-domains, not captured from real users.

**Method**

* The follow-up model only learns patterns it has seen in training: pairs within
  30 seconds, seen at least 5 times. It cannot help the first lookup of a
  brand-new domain or of a page it never saw. On Thapar, domains unseen in
  training gained only +3.2 points.
* It is first-order: it looks only at the last domain, not longer sequences.
* **Policy shift.** Once the model starts refreshing entries, the cache it
  observes changes. Online precision was about 5 to 7 points below the offline
  numbers on Thapar. The retraining loop reduces but does not remove this.
* Thresholds chosen on validation move slightly on the test window.

**Cost**

* Prediction costs upstream traffic. In the simulation the AI cache sends 0.504
  upstream queries per lookup against 0.358 for the normal cache (+41%), and 28%
  of its prefetches were never used. The gain is bought, not free.
* On the real Thapar data the decision tree added only **+1.4 points** of hit
  rate over the simple "refresh-if-used" rule at about the same upstream cost.
  Much of the benefit of refreshing can be had without ML; the ML's clearer
  contribution is choosing *which* entries to refresh more precisely, and the
  follow-up prediction.

**Evaluation and demo**

* The main evaluation is simulation (trace replay), not a production resolver;
  upstream latency is modelled.
* In the live demo, public DNS servers have their own cache, so a domain the AI
  cache prefetched may also come back faster for the normal cache. This works
  against the AI cache, so the live comparison is conservative. Live numbers vary
  between runs with network conditions.
* Not recreated in this rebuild: the FastAPI live web app, pcap/LANL adapters and
  the per-TTL-band workload analysis from the original.

---

## 6. How AI is used

AI is used **only for the two background decisions**, never on the lookup path.
All models are small and explainable, as required.

### 6.1 Follow-up prediction: transition probabilities

* **Type:** a first-order Markov / association-rule model, learned by counting.
* **Input:** the domain just looked up (and the client's recent history while
  learning).
* **Output:** a list of likely next domains with probabilities, e.g.
  `i.ytimg.com: 1.00`.
* **Explainability:** every prediction is two counts. The demo prints the
  reason in the form *"B followed A in c of n visits (within 30 s)"*, and saves
  it in `predictions.csv`.
* **Learning:** trained on history, and in the live demo it keeps updating
  online from new lookups.

### 6.2 Refresh prediction: decision tree

* **Type:** scikit-learn `DecisionTreeClassifier`, max depth 5, at least 50
  samples per leaf.
* **Input:** the 13 decision-time features in section 3.4.
* **Output:** probability that the expiring entry will be reused within its next
  TTL; refresh if ≥ 0.8.
* **Model selection:** compared offline with Gaussian **Naive Bayes** and
  **logistic regression** on the validation window:

  | Model (first round) | Precision | Recall | F1 | ROC-AUC |
  |---|---|---|---|---|
  | **Decision tree** | **0.69** | 0.71 | **0.70** | **0.78** |
  | Naive Bayes | 0.66 | 0.72 | 0.69 | 0.77 |
  | Logistic regression | 0.55 | 0.87 | 0.67 | 0.77 |

  The tree was chosen because it is as good or better and, crucially, its rules
  can be printed and read (`models/synth/tree_rules.txt`). After the two
  retraining rounds it scored precision 0.66, recall 0.71, F1 0.68, ROC-AUC 0.79.
* **What it learned (feature importance):** `mean_gap` (how often the name is
  usually asked for) 55%, `since_last_over_ttl` (how recently, relative to TTL)
  18%, `q_30m` 9%, `ttl` 7%, `total_q` 5%, `clients_30m` 4%. In plain words:
  *refresh names that are asked for often, were asked for recently, and by
  several clients*. The top rule is literally
  `mean_gap <= 129 s → mostly refresh`.
* **Speed:** the fitted tree is walked in plain Python (`TreePredictor`), a few
  microseconds per decision (about 12 µs measured in the original), so it does
  not slow the cache down.
* **Training:** labels from history (was the name really reused?), plus a
  DAgger-style retraining loop against policy shift (section 5).

### 6.3 Confidence thresholds

Both models give probabilities, and the thresholds are the dial between "more
hits" and "less wasted traffic". The project measured that trade-off rather
than guessing it: on the test window, a refresh threshold of 0.3 with
follow-ups gives an 89.4% hit rate but 0.95 upstream queries per lookup and 38%
prefetch precision, whereas 0.8 gives 83.9% at 0.504 queries per lookup and
72% precision. The threshold was picked on the validation window by the rule
"lowest threshold with at least 70% prefetch precision".

---

## 7. Key results

### 7.1 Large-scale simulation (rebuild, synthetic trace, test window)

36,000 lookups on the held-out test window, unlimited cache size:

| Strategy | Hit rate | Mean latency | P95 latency | Upstream per lookup | Prefetch precision | Unnecessary prefetches |
|---|---|---|---|---|---|---|
| Normal TTL cache | 64.1% | 40.5 ms | 127 ms | 0.358 | — | 0 |
| Unbound-style prefetch | 66.9% | 37.2 ms | 120 ms | 0.373 | 81% | 288 |
| Refresh-if-used rule | 75.3% | 28.1 ms | 95 ms | 0.479 | 60% | 3,363 |
| TTL cache + follow-up prefetch | 79.1% | 24.3 ms | 82 ms | 0.479 | 69% | 3,025 |
| ML refresh (decision tree) | 69.6% | 34.1 ms | 113 ms | 0.385 | 83% | 506 |
| **Final AI cache** | **83.9%** | **19.5 ms** | **76 ms** | **0.504** | **72%** | **3,518** |
| Oracle (knows the future) | 78.7% | 24.4 ms | 86 ms | 0.392 | 98% | 160 |

Headline findings:

* **Cache-hit ratio: 64.1% → 83.9%** (+19.8 points). Misses fall from 12,893 to
  5,688 (−56%).
* **Average lookup latency: 40.5 → 19.5 ms** (−52%); **P95: 127 → 76 ms**;
  P99: 952 → 366 ms.
* **Prediction accuracy:** follow-up predictions with p ≥ 0.7 came true 83% of
  the time (21,181 of 25,375). 72% of all prefetches/refreshes were used.
* **Upstream queries: 0.358 → 0.504 per lookup** (+41%). Demand queries (users
  waiting) fell from 12,893 to 5,688; background queries rose to 12,451.
* **Unnecessary prefetches:** 3,518 (28% of background fetches).
* Against the simple refresh-if-used rule, the AI cache gains **+8.7 points of
  hit rate for 5% more upstream traffic** and wastes about the same number of
  fetches, so the extra traffic buys real hits.
* The AI cache **beats the oracle**. The oracle only knows which *refreshes*
  will pay off; it cannot help the first lookup of a domain, which follow-up
  prediction can.
* With a small 40-entry LRU cache the ranking is the same (normal 62.3% → AI
  82.3%).

### 7.2 Live demo (113 lookups, `python demo.py run`)

A sample run with `--offline` (simulated upstream; with real DNS the latencies
change between runs but the pattern is the same):

| | Normal cache | AI cache |
|---|---|---|
| Hit rate | 56.6% (64 of 113) | **73.5%** (83 of 113) |
| Cache misses | 49 | **29** |
| Average lookup time | 68.3 ms | **34.5 ms** |
| P95 lookup time | 386.5 ms | **107.7 ms** |
| Total time users waited for DNS | 7.72 s | **3.90 s** |
| Upstream queries (misses + prefetches) | 49 + 0 = 49 | 29 + 20 = **49** |
| Prefetches used / unnecessary | — | 20 / 0 |
| Prediction accuracy | — | 52 of 64 (81%) |

In this run the AI cache halves DNS waiting time **with the same total number
of upstream queries**: every prefetch replaced a miss the user would otherwise
have waited for.

### 7.3 Original project on the real Thapar campus trace

From the original code (before the rebuild), on a real campus trace:

| Strategy | Hit rate | Mean latency | P95 latency | Upstream per lookup | Prefetch precision |
|---|---|---|---|---|---|
| Normal TTL cache | 58.5% | 323 ms | 1,790 ms | 0.400 | — |
| Unbound-style prefetch | 61.2% | 301 ms | 1,733 ms | 0.412 | 82% |
| Refresh-if-used rule | 68.5% | 244 ms | 1,562 ms | 0.484 | 65% |
| ML refresh (tree @0.5) | 69.4% | 238 ms | 1,549 ms | 0.478 | 70% |
| **Final AI cache** | **69.9%** | **234 ms** | **1,539 ms** | **0.484** | **69%** |
| Oracle | 72.4% | 214 ms | 1,483 ms | 0.426 | 99.5% |

On real traffic the gain is smaller (+11.4 points, −28% mean latency) but
consistent: stable across 3 latency seeds (+11.3 to +11.4 points), largest for
frequent domains (68.7% → 81.5%) and short TTLs ≤ 60 s (+13.1 points), and
smallest for domains not seen in training (+3.2 points) or with a large
300-entry cache (+5.5 points).

### 7.4 Conclusion

Query prediction **does** improve DNS resolution: substantially more lookups are
answered from the cache and lookup latency roughly halves in simulation, while
extra upstream traffic stays moderate (+41% per lookup in simulation, and no
extra in the live demo run) and about three quarters of prefetches are used.
The honest caveat is that on real data a simple refresh rule gets close to the
ML refresh; the follow-up prediction and the precision control are where the
AI adds the most.

---

## 8. What is novel

DNS prefetching itself is not new: browsers prefetch names in links on a page,
and resolvers such as Unbound refresh popular entries before they expire. What
this project does differently:

1. **Two complementary predictions in one cache.** Existing resolvers only
   *refresh* names already in the cache, and browsers only prefetch names
   written in the current page. This cache combines *refresh prediction* (which
   cached entries are worth keeping alive) with *follow-up prediction* (which
   names this client will need next, learned from behaviour, including names
   never written in any page, such as `i.ytimg.com` after `www.youtube.com`).
   The combination beats each part alone and even beats an oracle that only
   knows the future for refreshes.

2. **Learned, per-entry refresh decisions instead of a fixed rule.** Unbound
   uses one rule for every entry (hit in the last 10% of TTL). Here a model
   looks at each entry's history (popularity, recency relative to TTL, number of
   clients, TTL) and decides, with a confidence, whether a refresh is worth one
   upstream query.

3. **Explicit control of unnecessary traffic.** Prediction is tuned for cache
   performance per unit of network cost, not for model accuracy. The
   confidence threshold is chosen by a stated rule (at least 70% of prefetches
   must be useful), and the full hit-rate versus upstream-cost curve is
   reported, so the trade-off is visible instead of hidden.

4. **Explainable AI in a networking component.** Every follow-up prediction is
   justified by two counts, and the whole refresh model is a readable set of
   rules. A network administrator can see *why* a query was sent.

5. **Zero added latency on the lookup path.** The ML runs only in the
   background (after a lookup, or 2 s before expiry), with a pure-Python tree
   walk of a few microseconds, so a misprediction can waste a query but can
   never make a user's lookup slower.

6. **A fair, leak-free evaluation.** All strategies replay the same lookups;
   data is split by time; features use only information available at decision
   time; and the model is retrained under its own policy (DAgger-style) to
   handle the feedback loop where the model's own refreshes change what it sees.
   The comparison includes standard baselines (Unbound-style, refresh-if-used)
   and an oracle upper bound, not just "no cache vs our cache".

7. **Demonstrated on real DNS.** Beyond simulation, the same idea runs as a real
   cache and DNS server that answers `nslookup`, with a normal cache and the AI
   cache side by side on identical lookups, producing a per-lookup log, a
   per-prediction log with reasons, and an HTML report.

---

## Appendix: how to reproduce

```bat
cd dns-query-prediction
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run_all.py          :: simulation, models, dashboard (~1 minute)
python demo.py run         :: live demo with real DNS
python -m pytest           :: tests
```

Then open `dashboard\index.html` and `demo\output\report.html` in a browser.
More detail: [README.md](../README.md), [DEMO.md](DEMO.md), [RECONSTRUCTION.md](RECONSTRUCTION.md).
