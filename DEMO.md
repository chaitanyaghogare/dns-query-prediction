# Demo guide

The demo sends **real DNS queries** to a real DNS server and answers every
lookup twice: once with a normal TTL cache and once with the AI-assisted
cache. Both see exactly the same lookups, so the difference is caused only by
the prediction.

## One-time setup (before the presentation)

```bat
cd C:\Users\Hanuman\Documents\Chaitanya\CN
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python demo.py run
```

If the last command prints a table at the end, everything works. The demo
needs internet. If the network blocks DNS (some college networks do), try
`--upstream <your router's IP>`, and as a last resort `--offline` (answers
are then simulated and the outputs say so).

## Defined inputs and outputs

| | File | What it is |
|---|---|---|
| **Input 1** | `demo/input/history.csv` | 1,907 past lookups (time, client PC, domain) from 6 lab PCs over 4 hours. The AI learns from this. |
| **Input 2** | `demo/input/session.csv` | 113 lookups from a 10-minute browsing session (real sites: YouTube, GitHub, Stack Overflow, Wikipedia...). These are answered live. |
| Input 2 (alternative) | any `.txt` file, one domain per line | Your own list. `demo/input/my_domains.txt` is an example. |
| Input 2 (alternative) | typed by hand / `nslookup` | `interactive` and `serve` modes below. |
| **Output 1** | console | One line per lookup: normal cache result + time, AI cache result + time, and each prediction with its confidence. Then the comparison table. |
| **Output 2** | `demo/output/lookups.csv` | Every lookup: time, client, domain, HIT/MISS and ms for both caches, TTL, IP address. |
| **Output 3** | `demo/output/predictions.csv` | Every prediction: after which domain, predicted domain, confidence, prefetched or not, was it used, and why (counts). |
| **Output 4** | `demo/output/summary.json` | All the numbers. |
| **Output 5** | `demo/output/report.html` | One page with all of the above. Open it in a browser. |

## Suggested order on the day (about 5 minutes)

1. **Explain the input.** Open `demo/input/history.csv` and `session.csv` in Excel.
2. **Run the session.**
   ```bat
   python demo.py run --speed 20
   ```
   Point at the output as it runs:
   - `[1]` the real DNS server being used.
   - `[2]` the rules learned, e.g. *after www.youtube.com -> i.ytimg.com p=1.00*.
   - `[3]` lines like
     `github.com ... normal: MISS 30 ms | AI: MISS 30 ms` followed by
     `predicts github.githubassets.com next (confidence 90%) -> prefetched`, and a moment later
     `github.githubassets.com ... normal: MISS 126 ms | AI: HIT 0.05 ms <- answered from a prefetch`.
   - `[4]` the comparison table: hit rate, average and P95 lookup time, upstream
     queries, prefetches used vs unnecessary, prediction accuracy.
3. **Open `demo\output\report.html`** to show the same results as a page.
4. **Live, typed by you or the examiner:**
   ```bat
   python demo.py interactive
   domain> www.youtube.com
   domain> i.ytimg.com
   domain> stats
   domain> quit
   ```
   The second lookup comes back as a HIT because the first one predicted it.
5. **As a real DNS server (optional).** In one window:
   ```bat
   python demo.py serve
   ```
   In a second window:
   ```bat
   nslookup -port=5300 stackoverflow.com 127.0.0.1
   nslookup -port=5300 cdn.sstatic.net 127.0.0.1
   ```
   The server window shows each query, the prediction and the hit. Ctrl+C
   writes the report.
6. **Simulation results.** `dashboard\index.html` has the large-scale
   comparison of 7 strategies on 83,000 lookups (`python run_all.py`).

## Useful options

| Option | Effect |
|---|---|
| `--speed 20` | play the session at 20x real time so it can be followed (default: as fast as possible) |
| `--domains file.txt` | use your own list instead of session.csv |
| `--threshold 0.9` | only prefetch when very confident (fewer, safer prefetches) |
| `--upstream 8.8.8.8` | choose the DNS server (default: this PC's DNS server, then 8.8.8.8, 1.1.1.1) |
| `--offline` | no network needed; answers are simulated |
| `python demo.py make-input --seed 5` | generate new history/session files (edit `src/dnscache/demo/sites.py` to change the sites) |

## Things to expect

* Numbers change a little between runs because real network latency changes.
* The first lookup of any domain is always a MISS in both caches. The AI cache
  wins on the *next* domains of a page, which it predicted.
* Public DNS servers have their own cache, so a domain the AI cache prefetched
  may come back faster for the normal cache too. This works against the AI
  cache, so the comparison is conservative.
* Prefetches cost upstream queries; the "unnecessary" row shows how many were
  wasted.
