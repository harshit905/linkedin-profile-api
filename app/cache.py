"""In-process TTL cache.

Caching matters here for more than latency: every cache hit is one fewer
request against LinkedIn, which is the resource most likely to rate-limit or
challenge us. A single dyno holds this in memory; swap in Redis behind the
same two methods if you scale past one instance.
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int, max_entries: int):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if expires_at < time.monotonic():
                del self._store[key]
                self.misses += 1
                return None
            # Refresh recency for the LRU eviction below.
            self._store.move_to_end(key)
            self.hits += 1
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic() + self._ttl, value)
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._store), "hits": self.hits, "misses": self.misses}
