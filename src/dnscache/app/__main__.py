"""Serve the dashboard locally:  python -m dnscache.app [--open] [--port 8000]

The dashboard is a single static file, so this only needs the standard
library. Rebuild it first with ``python -m dnscache.run``.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--open", action="store_true")
    a = ap.parse_args(argv)
    d = ROOT / "dashboard"
    if not (d / "index.html").exists():
        raise SystemExit("dashboard/index.html not found - run `python -m dnscache.run` first")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(d))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), handler)
    url = f"http://127.0.0.1:{a.port}/"
    print(f"Dashboard at {url}  (Ctrl+C to stop)")
    if a.open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
