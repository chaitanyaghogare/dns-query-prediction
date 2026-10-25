"""Real websites and the extra domains a browser looks up when it opens them.

This catalogue is the ground truth used to *generate* the demo inputs (the
model never sees it; it only sees the resulting lookup history). Edit it, then
run ``python demo.py make-input`` to regenerate the input files.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

# site -> [(domain loaded with it, chance it is loaded on a visit)]
SITES = {
    "www.google.com": [("www.gstatic.com", 0.95), ("apis.google.com", 0.7), ("ssl.gstatic.com", 0.6)],
    "www.youtube.com": [("i.ytimg.com", 0.95), ("yt3.ggpht.com", 0.85), ("fonts.googleapis.com", 0.6), ("www.gstatic.com", 0.7)],
    "github.com": [("github.githubassets.com", 0.95), ("avatars.githubusercontent.com", 0.9), ("api.github.com", 0.6), ("collector.github.com", 0.5)],
    "stackoverflow.com": [("cdn.sstatic.net", 0.95), ("i.sstatic.net", 0.8), ("ajax.googleapis.com", 0.6)],
    "en.wikipedia.org": [("upload.wikimedia.org", 0.9), ("login.wikimedia.org", 0.5)],
    "www.amazon.in": [("m.media-amazon.com", 0.95), ("images-eu.ssl-images-amazon.com", 0.8), ("fls-eu.amazon.in", 0.5)],
    "www.reddit.com": [("www.redditstatic.com", 0.95), ("styles.redditmedia.com", 0.7), ("preview.redd.it", 0.6)],
    "www.bbc.com": [("static.files.bbci.co.uk", 0.9), ("ichef.bbci.co.uk", 0.85)],
    "www.linkedin.com": [("static.licdn.com", 0.95), ("media.licdn.com", 0.8)],
    "www.geeksforgeeks.org": [("media.geeksforgeeks.org", 0.9), ("www.googletagmanager.com", 0.7)],
    "mail.google.com": [("ssl.gstatic.com", 0.9), ("accounts.google.com", 0.7), ("www.gstatic.com", 0.6)],
    "www.netflix.com": [("assets.nflxext.com", 0.95), ("codex.nflxext.com", 0.7)],
    "www.flipkart.com": [("rukminim2.flixcart.com", 0.9), ("static-assets-web.flixcart.com", 0.8)],
    "web.whatsapp.com": [("static.whatsapp.net", 0.9), ("pps.whatsapp.net", 0.6)],
    "www.w3schools.com": [("fonts.googleapis.com", 0.8), ("www.googletagmanager.com", 0.6)],
    "leetcode.com": [("assets.leetcode.com", 0.9)],
    "www.coursera.org": [("d3njjcbhbojbot.cloudfront.net", 0.8)],
    "docs.python.org": [("www.python.org", 0.3)],
}
# visited once in a while; no caching or prediction can help with these
ONE_OFF = [
    "www.iana.org", "www.ietf.org", "www.rfc-editor.org", "www.nasa.gov", "www.who.int", "www.isro.gov.in",
    "nptel.ac.in", "www.irctc.co.in", "www.mit.edu", "www.stanford.edu", "www.cloudflare.com", "www.mozilla.org",
    "www.apache.org", "www.debian.org", "www.kernel.org", "www.w3.org", "www.npmjs.com", "pypi.org",
    "ubuntu.com", "www.cisco.com", "www.wireshark.org", "www.isc.org", "www.icann.org", "www.arin.net",
]
BACKGROUND = ["time.windows.com", "www.msftconnecttest.com"]

CLIENTS = [f"192.168.1.{i}" for i in range(11, 17)]   # six lab PCs


def generate(minutes: float, seed: int, sessions_per_client_hour: float = 12.0) -> list[tuple[float, str, str]]:
    """Browsing lookups (time_s, client, domain) for all clients over `minutes`."""
    rng = random.Random(seed)
    sites = list(SITES)
    weights = [1 / (i + 1) ** 0.9 for i in range(len(sites))]
    end = minutes * 60
    rows = []
    for client in CLIENTS:
        favs = rng.sample(sites, 7)
        t = rng.uniform(0, 60)
        while t < end:
            site = rng.choice(favs) if rng.random() < 0.75 else rng.choices(sites, weights)[0]
            v = t
            for _ in range(1 + int(rng.expovariate(1 / 1.5))):      # page views on this site
                rows.append((v, client, site))
                for dom, p in SITES[site]:
                    if rng.random() < p:
                        rows.append((v + rng.uniform(0.15, 1.5), client, dom))
                v += rng.expovariate(1 / 40)
            if rng.random() < 0.2:
                rows.append((t + rng.uniform(0, 30), client, rng.choice(ONE_OFF)))
            t += rng.expovariate(sessions_per_client_hour / 3600)
        for name in BACKGROUND:
            t = rng.uniform(0, 600)
            while t < end:
                rows.append((t, client, name))
                t += rng.uniform(500, 700)
    rows = [(round(t, 2), c, d) for t, c, d in rows if t < end]
    rows.sort()
    return rows


def write_csv(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "client", "domain"])
        w.writerows(rows)


def make_inputs(folder: Path, seed: int = 11) -> tuple[Path, Path]:
    """history.csv = 4 h of earlier lab traffic (training input); session.csv = 10 min live demo."""
    hist, sess = folder / "history.csv", folder / "session.csv"
    write_csv(hist, generate(240, seed))
    write_csv(sess, generate(10, seed + 1))
    return hist, sess
