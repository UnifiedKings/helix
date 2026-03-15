from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Optional


@dataclass
class _Window:
    start: float
    count: int


class FixedWindowRateLimiter:
    """Simple in-process fixed-window rate limiter.

    This is intentionally lightweight (no Redis). It limits abuse in a single-worker
    deployment and provides a baseline even when multiple workers are used.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict[str, _Window] = {}

    def allow(self, key: str, *, limit: int, window_s: int) -> bool:
        now = time.time()
        window_s = max(1, int(window_s))
        limit = max(1, int(limit))
        k = key or ""
        if not k:
            return True
        with self._lock:
            w = self._buckets.get(k)
            if not w or (now - w.start) >= window_s:
                self._buckets[k] = _Window(start=now, count=1)
                # opportunistic cleanup
                if len(self._buckets) > 10000:
                    self._prune_locked(now, window_s * 4)
                return True
            if w.count >= limit:
                return False
            w.count += 1
            return True

    def _prune_locked(self, now: float, max_age_s: int) -> None:
        dead = []
        for k, w in self._buckets.items():
            if (now - w.start) > max_age_s:
                dead.append(k)
        for k in dead:
            self._buckets.pop(k, None)


RATE_LIMITER = FixedWindowRateLimiter()


def make_key(*, scope: str, user_id: Optional[str], ip: str) -> str:
    scope = (scope or "").strip()
    uid = (user_id or "").strip()
    ip = (ip or "").strip()
    # Prefer user id; fall back to IP (still useful for unauth endpoints).
    ident = uid or ip or "anon"
    return f"{scope}:{ident}"
