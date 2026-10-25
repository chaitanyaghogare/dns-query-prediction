"""Seeded synthetic DNS workload generator.

The workload is built from things real DNS traffic is known to contain:

* **apps/websites** with Zipf popularity. Opening one queries its root name and,
  shortly afterwards, its dependent names (CDN, API, images, analytics...).
  These follow-ups are what the sequence model learns.
* **revisits** inside a browsing session, so names come back within their TTL
  (normal cache hits) or just after it (where refresh/prefetch helps).
* **periodic background polling** (push, update, NTP-like services).
* **one-off long-tail names** that no cache can help with.
* a **day/night activity cycle**.

t = 0 is Monday 00:00. Everything is controlled by ``seed``.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..data.schema import Query, cache_key

BASES = [
    "vidtube", "shopkart", "newsly", "chatter", "mailbox", "campusportal", "codehub",
    "musicly", "photogram", "wikinfo", "streamflix", "cricscore", "foodiego", "ridenow",
    "paywallet", "learnhub", "gamezone", "weatherly", "mapsy", "jobfinder", "travelix",
    "bankone", "docsync", "meetly", "tweetr", "blogspace", "sportsnet", "movietix",
    "quizly", "fitpulse", "cloudbox", "podcastr", "recipebook", "stockwise", "askforum",
    "notesapp", "translatr", "printhub", "libcat", "eventsy",
]
SUBS = ["cdn", "api", "img", "static", "media", "auth", "assets", "video", "edge", "cache"]
THIRD_PARTY = [
    "fonts.typeface-cdn.net", "ads.adtrackr.net", "analytics.metricsy.io",
    "cdnjs.libcloud.org", "login.idprovider.com", "tag.pixelserve.net",
]
POLLERS = [
    ("push.osvendor.com", 120), ("time.ntpsync.org", 600), ("update.osvendor.com", 900),
    ("sync.cloudbox.com", 300), ("chat-presence.chatter.com", 60), ("mail-check.mailbox.com", 180),
]
TTL_CHOICES = [(20, 0.08), (30, 0.12), (60, 0.30), (300, 0.32), (900, 0.08), (3600, 0.10)]


@dataclass
class SynthConfig:
    seed: int = 7
    hours: float = 48.0
    n_clients: int = 40
    n_apps: int = 60
    zipf_a: float = 1.05
    peak_sessions_per_client_hour: float = 5.0
    favourites_per_client: int = 8
    views_mean: float = 4.0
    view_gap_mean_s: float = 45.0
    longtail_prob: float = 0.35
    pollers_per_client: int = 1


@dataclass
class App:
    root: str
    followers: list = field(default_factory=list)   # (name, prob, delay_lo, delay_hi)


def _pick_ttl(rng: random.Random) -> int:
    r, acc = rng.random(), 0.0
    for ttl, w in TTL_CHOICES:
        acc += w
        if r <= acc:
            return ttl
    return TTL_CHOICES[-1][0]


def activity(hour_of_day: float) -> float:
    """Relative activity level, ~0.08 at 4 am, 1.0 around 8 pm."""
    x = math.cos((hour_of_day - 20.0) / 24.0 * 2 * math.pi)
    v = 0.55 + 0.45 * x
    if 1 <= hour_of_day < 7:
        v *= 0.25
    return max(0.05, v)


def build_catalogue(cfg: SynthConfig, rng: random.Random) -> tuple[list[App], dict[str, int]]:
    apps, ttl = [], {}
    for i in range(cfg.n_apps):
        base = BASES[i % len(BASES)] + ("" if i < len(BASES) else str(i // len(BASES) + 1))
        app = App(root=f"www.{base}.com")
        for sub in rng.sample(SUBS, rng.randint(1, 5)):
            delayed = rng.random() < 0.2
            lo, hi = (3.0, 20.0) if delayed else (0.05, 2.0)
            app.followers.append((f"{sub}.{base}.com", rng.uniform(0.55, 0.98), lo, hi))
        for tp in rng.sample(THIRD_PARTY, rng.randint(0, 2)):
            app.followers.append((tp, rng.uniform(0.3, 0.8), 0.2, 4.0))
        apps.append(app)
    names = {a.root for a in apps} | {f[0] for a in apps for f in a.followers} | {p[0] for p in POLLERS}
    for n in sorted(names):
        ttl[n] = _pick_ttl(rng)
    return apps, ttl


def generate(cfg: SynthConfig | None = None) -> tuple[list[Query], dict[str, int]]:
    """Return (queries sorted by time, {cache_key: ttl})."""
    cfg = cfg or SynthConfig()
    rng = random.Random(cfg.seed)
    apps, ttl = build_catalogue(cfg, rng)
    weights = [1.0 / (i + 1) ** cfg.zipf_a for i in range(len(apps))]
    end = cfg.hours * 3600
    out: list[Query] = []
    longtail_n = 0

    for c in range(cfg.n_clients):
        client = f"10.0.{c // 250}.{c % 250 + 10}"
        favs = set()
        while len(favs) < cfg.favourites_per_client:
            favs.add(rng.choices(range(len(apps)), weights)[0])
        favs = sorted(favs)
        lazy = rng.uniform(0.5, 1.5)            # some clients are busier than others

        # browsing sessions: thinned Poisson process following the day cycle
        t = 0.0
        lam_max = cfg.peak_sessions_per_client_hour * lazy / 3600
        while True:
            t += rng.expovariate(lam_max)
            if t >= end:
                break
            if rng.random() > activity((t / 3600) % 24):
                continue
            app = apps[rng.choice(favs)] if rng.random() < 0.8 else apps[rng.choices(range(len(apps)), weights)[0]]
            v_t = t
            for _ in range(1 + int(rng.expovariate(1 / (cfg.views_mean - 1)))):
                out.append(Query(v_t, client, app.root))
                for name, p, lo, hi in app.followers:
                    if rng.random() < p:
                        out.append(Query(v_t + rng.uniform(lo, hi), client, name))
                v_t += rng.expovariate(1 / cfg.view_gap_mean_s)
            if rng.random() < cfg.longtail_prob:
                longtail_n += 1
                out.append(Query(t + rng.uniform(0, 60), client, f"x{longtail_n:06d}.longtail-{rng.randint(1, 50)}.net"))

        # background pollers (only while the device is "awake")
        for name, period in rng.sample(POLLERS, cfg.pollers_per_client):
            t = rng.uniform(0, period)
            while t < end:
                if rng.random() < 0.2 + 0.8 * activity((t / 3600) % 24):
                    out.append(Query(t, client, name))
                t += period * rng.uniform(0.8, 1.2)

    # round like the CSV does, so a generated trace and a reloaded one are identical
    out = [Query(round(q.ts, 3), q.client, q.qname, q.qtype) for q in out if q.ts < end]
    out.sort(key=lambda q: (q.ts, q.client, q.qname))
    ttl_by_key = {cache_key(n): v for n, v in ttl.items()}
    return out, ttl_by_key


def save_catalogue(path: str | Path, ttl_by_key: dict[str, int], cfg: SynthConfig) -> None:
    Path(path).write_text(json.dumps({"config": asdict(cfg), "ttl": ttl_by_key}, indent=1))
