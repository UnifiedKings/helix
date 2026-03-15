from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Generic, Optional, TypeVar


T = TypeVar("T")


@dataclass
class _Entry(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    """A small in-memory LRU-ish TTL cache.

    Purpose:
      - reduce upstream calls (MusicBrainz/Wikidata/CoverArtArchive)
      - keep UI search responsive

    Notes:
      - This is process-local. If you run multiple workers, each worker will have its own cache.
      - Keep sizes modest; use it for JSON, not large binaries.
    """

    def __init__(self, max_items: int = 2048):
        self._max_items = max_items
        self._data: OrderedDict[str, _Entry[T]] = OrderedDict()

    def get(self, key: str) -> Optional[T]:
        now = time.time()
        ent = self._data.get(key)
        if not ent:
            return None
        if ent.expires_at < now:
            self._data.pop(key, None)
            return None
        # mark as recently used
        self._data.move_to_end(key)
        return ent.value

    def set(self, key: str, value: T, ttl_seconds: int) -> None:
        expires_at = time.time() + max(1, int(ttl_seconds))
        self._data[key] = _Entry(value=value, expires_at=expires_at)
        self._data.move_to_end(key)
        while len(self._data) > self._max_items:
            self._data.popitem(last=False)


    def delete(self, key: str) -> None:
        """Remove a key from the cache, if present."""
        self._data.pop(key, None)

    def get_or_set(self, key: str, ttl_seconds: int, fn: Callable[[], T]) -> T:
        hit = self.get(key)
        if hit is not None:
            return hit
        value = fn()
        self.set(key, value, ttl_seconds)
        return value
