"""The cache itself: TTL expiry + optional LRU capacity limit."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class Entry:
    key: str
    ttl: int
    fetched_at: float          # when the upstream request for this lifetime was sent
    ready_at: float            # when the answer arrived (queries before this wait: "in flight")
    expires_at: float
    prefetched: bool = False   # this lifetime came from a refresh/prefetch, not a demand miss
    hits: int = 0              # hits during this lifetime
    gen: int = 0               # bumped on every new lifetime; stale timer events are ignored
    pending_ready: float | None = None   # a refresh is in flight and lands at this time
    pending_fetched: float | None = None
    unused_refreshes: int = 0  # consecutive refreshed lifetimes without a hit
    pending_waiters: int = 0   # lookups waiting for the in-flight refresh
    source: str = "demand"     # demand / refresh / followup


class Cache:
    def __init__(self, capacity: int | None = None):
        self.capacity = capacity
        self.entries: "OrderedDict[str, Entry]" = OrderedDict()

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def get(self, key: str) -> Entry | None:
        return self.entries.get(key)

    def touch(self, key: str) -> None:
        self.entries.move_to_end(key)

    def put(self, entry: Entry) -> list[Entry]:
        """Insert (or replace) an entry; returns entries evicted by LRU."""
        self.entries[entry.key] = entry
        self.entries.move_to_end(entry.key)
        evicted = []
        if self.capacity is not None:
            while len(self.entries) > self.capacity:
                _, old = self.entries.popitem(last=False)
                evicted.append(old)
        return evicted

    def remove(self, key: str) -> Entry | None:
        return self.entries.pop(key, None)
