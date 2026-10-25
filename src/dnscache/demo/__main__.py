"""Live demo: real DNS lookups through a normal cache and an AI-assisted cache.

  python demo.py run                      replay the demo session (input: demo/input/session.csv)
  python demo.py run --domains my.txt     your own list of domains
  python demo.py interactive              type domains yourself
  python demo.py serve                    be a real DNS server for nslookup on port 5300
  python demo.py make-input               regenerate the input files

Every run first learns follow-up rules from demo/input/history.csv, then
answers each lookup with both caches, prints one line per lookup and writes
demo/output/ (lookups.csv, predictions.csv, summary.json, report.html).
Add --offline if the network blocks DNS (answers are then simulated).
"""
from __future__ import annotations

import argparse
import csv
import socket
import sys
import time
from pathlib import Path

from ..collect import dnsmsg
from ..data.schema import Query, cache_key, normalise_name
from ..predict.sequence import SequenceModel
from . import report, sites
from .livecache import LiveCache
from .upstream import Answer, choose_upstream

ROOT = Path(__file__).resolve().parents[3]
DEMO = ROOT / "demo"
LINE = "-" * 100


# --------------------------------------------------------------------- inputs
def read_lookups(path: Path) -> list[tuple[float, str, str]]:
    """CSV with time_s,client,domain  -or-  a text file with one domain per line
    (optionally '<seconds to wait> <domain>'; # starts a comment)."""
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8") as f:
            return [(float(r["time_s"]), r.get("client") or "you", normalise_name(r["domain"])) for r in csv.DictReader(f)]
    out, t = [], 0.0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#")[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) == 2:
            t += float(parts[0])
            dom = parts[1]
        else:
            t += 1.0
            dom = parts[0]
        out.append((round(t, 2), "you", normalise_name(dom)))
    return out


def learn(history: Path, window_s: float, min_support: int, log=print) -> SequenceModel:
    rows = read_lookups(history)
    model = SequenceModel(window_s=window_s, min_support=min_support).fit(
        [Query(t, c, d) for t, c, d in rows])
    rules = sorted(((a, b, p) for a, fol in model.table().items() for b, p in fol), key=lambda x: -x[2])
    log(f"  read {len(rows):,} past lookups from {len({c for _, c, _ in rows})} clients ({history.name})")
    log(f"  learned {len(rules)} follow-up rules; strongest:")
    for a, b, p in rules[:8]:
        log(f"    after {a.split('|')[0]:<26} -> {b.split('|')[0]:<32} p={p:.2f}")
    model._meta = {"history_lookups": len(rows), "history_clients": len({c for _, c, _ in rows}),
                   "rules_from_history": len(rules)}
    model._table = None            # keep learning online during the demo
    return model


class SharedFetch:
    """Both caches use one upstream. If both miss on the same lookup they share one real query."""

    def __init__(self, upstream):
        self.up, self.memo = upstream, {}

    def new_lookup(self):
        self.memo = {}

    def _get(self, name, qtype):
        for attempt in range(2):
            try:
                return self.up.fetch(name, qtype)
            except OSError:
                if attempt:
                    return Answer(None, [], 0, self.up.timeout * 1000 if hasattr(self.up, "timeout") else 0, "TIMEOUT")

    def __call__(self, name, qtype="A", prefetch=False):
        if prefetch:
            return self._get(name, qtype)
        k = (name, qtype)
        if k not in self.memo:
            self.memo[k] = self._get(name, qtype)
        return self.memo[k]


def fmt(r) -> str:
    return f"{r.outcome:<4} {r.ms:8.2f} ms"


def print_lookup(a, b, ai: LiveCache, n_pred_before: int) -> None:
    note = "  <- answered from a prefetch" if b.from_prefetch and b.outcome != "MISS" else ""
    print(f"{a.t:7.1f}s  {a.client:<13} {a.domain:<34} normal: {fmt(a)} | AI: {fmt(b)}{note}")
    for p in ai.predictions[n_pred_before:]:
        if p.action == "prefetched":       # predictions for domains already cached are only logged to CSV
            print(f"{'':23}predicts {p.domain} next (confidence {p.prob:.0%}) -> prefetched")


def print_comparison(s: dict) -> None:
    print(LINE)
    print(f"{'RESULT':<62}{'Normal cache':>18}{'AI cache':>20}")
    print(LINE)
    for k, v1, v2 in report.comparison_rows(s):
        print(f"{k:<62}{v1:>18}{v2:>20}")
    print(LINE)


def setup(args, clock=None):
    print(LINE)
    print("[1] UPSTREAM DNS SERVER")
    up = choose_upstream(args.upstream, args.offline, log=print)
    print(f"  using {up}" + ("   ** OFFLINE: answers are simulated, not real DNS **" if args.offline else "   (real DNS over UDP)"))
    print("[2] LEARNING FROM HISTORY")
    model = learn(Path(args.history), args.window, args.min_support)
    normal = LiveCache("normal", clock=clock)
    ai = LiveCache("ai", predictor=model, threshold=args.threshold, clock=clock)
    return up, model, normal, ai


def finish(args, up, model, normal, ai, session_name: str, started: str) -> None:
    meta = {"started": started, "upstream": str(up), "threshold": args.threshold, "window_s": args.window,
            "history_file": str(Path(args.history).name), "session_file": session_name, **model._meta}
    s = report.write_outputs(Path(args.out), meta, normal, ai, model, args.window)
    print_comparison(s)
    out = Path(args.out)
    print("OUTPUT FILES")
    for f in ("report.html", "lookups.csv", "predictions.csv", "summary.json"):
        print(f"  {out / f}")


# --------------------------------------------------------------------- commands
def cmd_run(args) -> None:
    started = time.strftime("%Y-%m-%d %H:%M")
    up, model, normal, ai = setup(args)
    src = Path(args.domains or args.session)
    lookups = read_lookups(src)
    print(f"[3] ANSWERING {len(lookups)} LOOKUPS FROM {src.name}  (time = seconds into the session)")
    print(LINE)
    fetch = SharedFetch(up)
    prev = None
    for t, client, dom in lookups:
        if args.speed and prev is not None:
            time.sleep(max(0.0, (t - prev) / args.speed))
        prev = t
        fetch.new_lookup()
        k = len(ai.predictions)
        a = normal.lookup(t, client, dom, fetch)
        b = ai.lookup(t, client, dom, fetch)
        print_lookup(a, b, ai, k)
    print("[4] RESULTS")
    finish(args, up, model, normal, ai, src.name, started)


def cmd_interactive(args) -> None:
    started = time.strftime("%Y-%m-%d %H:%M")
    t0 = time.time()
    up, model, normal, ai = setup(args, clock=lambda: time.time() - t0)
    print("[3] TYPE DOMAINS (several per line is fine). 'stats' shows the comparison, 'quit' ends and writes the report.")
    fetch = SharedFetch(up)
    while True:
        try:
            line = input("domain> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line in ("quit", "exit", "q"):
            break
        if line == "stats":
            print_comparison({**{"normal": normal.summary(), "ai": ai.summary()},
                              "prediction_accuracy": report.prediction_accuracy(ai.predictions, ai.results, args.window)})
            continue
        for dom in line.split():
            fetch.new_lookup()
            now = time.time() - t0
            k = len(ai.predictions)
            a = normal.lookup(now, "you", normalise_name(dom), fetch)
            b = ai.lookup(now, "you", normalise_name(dom), fetch)
            print_lookup(a, b, ai, k)
            if b.addresses:
                print(f"{'':23}answer: {', '.join(b.addresses[:4])}  (TTL {b.ttl_left} s)")
    if normal.results:
        finish(args, up, model, normal, ai, "typed by hand", started)


def cmd_serve(args) -> None:
    if args.offline:
        raise SystemExit("serve needs a real upstream DNS server (drop --offline)")
    started = time.strftime("%Y-%m-%d %H:%M")
    t0 = time.time()
    up, model, normal, ai = setup(args, clock=lambda: time.time() - t0)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(0.5)
    print(f"[3] DNS SERVER LISTENING ON {args.host}:{args.port}. In another window try:")
    print(f"      nslookup -port={args.port} www.youtube.com {args.host}")
    print(f"      nslookup -port={args.port} i.ytimg.com {args.host}")
    print("    Ctrl+C stops the server and writes the report.")
    print(LINE)
    fetch = SharedFetch(up)
    try:
        while True:
            try:
                pkt, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                qid, name, qtype = dnsmsg.parse_question(pkt)
            except (ValueError, IndexError):
                continue
            fetch.new_lookup()
            now = time.time() - t0
            k = len(ai.predictions)
            a = normal.lookup(now, addr[0], name, fetch, qtype)
            b = ai.lookup(now, addr[0], name, fetch, qtype)
            if b.response:
                age = max(0, int(now - b.stored_at)) if b.outcome != "MISS" else 0
                sock.sendto(dnsmsg.rewrite(b.response, qid, age), addr)
            print_lookup(a, b, ai, k)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    if normal.results:
        finish(args, up, model, normal, ai, f"live nslookup queries on port {args.port}", started)


def cmd_make_input(args) -> None:
    h, s = sites.make_inputs(Path(args.folder), args.seed)
    print(f"wrote {h} and {s}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="demo.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--history", default=str(DEMO / "input" / "history.csv"), help="past lookups to learn from")
    common.add_argument("--upstream", help="DNS server IP (default: this computer's DNS server, then 8.8.8.8, 1.1.1.1)")
    common.add_argument("--offline", action="store_true", help="no network: simulate the upstream server")
    common.add_argument("--threshold", type=float, default=0.7, help="prefetch when confidence >= this (default 0.7)")
    common.add_argument("--window", type=float, default=30.0, help="follow-up window in seconds")
    common.add_argument("--min-support", type=int, default=5)
    common.add_argument("--out", default=str(DEMO / "output"))
    r = sub.add_parser("run", parents=[common], help="answer a recorded session or a list of domains")
    r.add_argument("--session", default=str(DEMO / "input" / "session.csv"))
    r.add_argument("--domains", help="text file: one domain per line, optionally '<wait seconds> <domain>'")
    r.add_argument("--speed", type=float, default=0, help="play back at N x real time (0 = as fast as possible)")
    sub.add_parser("interactive", parents=[common], help="type domains yourself")
    s = sub.add_parser("serve", parents=[common], help="act as a DNS server for nslookup / dig")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=5300)
    m = sub.add_parser("make-input", help="regenerate demo/input/history.csv and session.csv")
    m.add_argument("--folder", default=str(DEMO / "input"))
    m.add_argument("--seed", type=int, default=11)
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    {"run": cmd_run, "interactive": cmd_interactive, "serve": cmd_serve, "make-input": cmd_make_input}[args.cmd](args)


if __name__ == "__main__":
    main()
