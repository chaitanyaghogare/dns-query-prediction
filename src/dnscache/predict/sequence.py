"""Follow-up (transition probability) model.

For every lookup of name A by a client, which other names B did the same
client ask for within the next ``window_s`` seconds? Then

    P(B follows A) = #(A occurrences followed by B) / #(A occurrences)

This is a first-order Markov / association-rule model: fully explainable,
every prediction can be traced back to two counts.
"""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path


class SequenceModel:
    def __init__(self, window_s: float = 30.0, min_support: int = 5, max_followers: int = 8):
        self.window_s = window_s
        self.min_support = min_support
        self.max_followers = max_followers
        self.count: dict[str, int] = defaultdict(int)
        self.pair: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._table: dict[str, list[tuple[str, float]]] | None = None

    # ------------------------------------------------------------ training
    def observe(self, q) -> None:
        """Learn from one lookup (lookups must arrive in time order)."""
        if not hasattr(self, "_pending"):
            self._pending = defaultdict(deque)          # client -> (ts, key, followers seen)
        dq = self._pending[q.client]
        while dq and q.ts - dq[0][0] > self.window_s:
            dq.popleft()
        for ts, a, seen in dq:
            if q.key != a and q.key not in seen:
                seen.add(q.key)
                self.pair[a][q.key] += 1
        self.count[q.key] += 1
        dq.append((q.ts, q.key, set()))
        self._table = None

    def fit(self, queries) -> "SequenceModel":
        """Batch fit on a list of Query records sorted by time."""
        for q in queries:
            self.observe(q)
        self.table()                            # freeze: fast lookups from now on
        return self

    # ------------------------------------------------------------ prediction
    def table(self) -> dict[str, list[tuple[str, float]]]:
        if self._table is None:
            tab = {}
            for a, n in self.count.items():
                if n < self.min_support:
                    continue
                fol = [(b, c / n) for b, c in self.pair.get(a, {}).items() if c >= self.min_support]
                fol.sort(key=lambda x: -x[1])
                if fol:
                    tab[a] = fol[: self.max_followers]
            self._table = tab
        return self._table

    def predict(self, key: str, threshold: float = 0.0) -> list[tuple[str, float]]:
        if self._table is not None:
            return [(b, p) for b, p in self._table.get(key, []) if p >= threshold]
        n = self.count.get(key, 0)              # model still learning: compute just this row
        if n < self.min_support:
            return []
        fol = sorted(((b, c / n) for b, c in self.pair.get(key, {}).items()
                      if c >= self.min_support and c / n >= threshold), key=lambda x: -x[1])
        return fol[: self.max_followers]

    def explain(self, a: str, b: str) -> str:
        n, c = self.count.get(a, 0), self.pair.get(a, {}).get(b, 0)
        return f"{b.split('|')[0]} followed {a.split('|')[0]} in {c} of {n} visits (within {self.window_s:.0f} s)"

    # ------------------------------------------------------------ storage
    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            "window_s": self.window_s, "min_support": self.min_support, "max_followers": self.max_followers,
            "count": {a: n for a, n in self.count.items() if n >= self.min_support},
            "pair": {a: {b: c for b, c in d.items() if c >= self.min_support}
                     for a, d in self.pair.items() if self.count[a] >= self.min_support},
        }))

    @classmethod
    def load(cls, path: str | Path) -> "SequenceModel":
        d = json.loads(Path(path).read_text())
        m = cls(d["window_s"], d["min_support"], d["max_followers"])
        m.count.update(d["count"])
        for a, fol in d["pair"].items():
            m.pair[a].update(fol)
        return m
