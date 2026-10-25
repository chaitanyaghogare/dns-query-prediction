"""Canonical trace schema (v1).

Every data source is converted into a list of ``Query`` records sorted by
timestamp. Timestamps are seconds from the start of the trace; ``t = 0`` is
Monday 00:00 so that hour-of-day and day-of-week features can be derived.
"""
from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = 1
CSV_COLUMNS = ["ts", "client", "qname", "qtype"]


@dataclass(frozen=True)
class Query:
    ts: float
    client: str
    qname: str
    qtype: str = "A"

    @property
    def key(self) -> str:
        return cache_key(self.qname, self.qtype)


def cache_key(qname: str, qtype: str = "A") -> str:
    return f"{normalise_name(qname)}|{qtype.upper()}"


def normalise_name(qname: str) -> str:
    return qname.strip().rstrip(".").lower()


def split_key(key: str) -> tuple[str, str]:
    name, _, qtype = key.partition("|")
    return name, qtype or "A"
