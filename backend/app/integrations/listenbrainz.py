from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

import httpx

from ..cache import TTLCache

LB_BASE = "https://api.listenbrainz.org"


class ListenBrainzClient:
    """Thin async client with basic politeness throttling.

    ListenBrainz returns X-RateLimit-* headers; we also keep a small
    min-interval throttle to be friendly.
    """

    def __init__(self, user_agent: str, min_interval_ms: int = 250, timeout_s: int = 20):
        self._user_agent = user_agent
        self._min_interval = max(0.0, float(min_interval_ms) / 1000.0)
        self._timeout = timeout_s
        self._lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._client = httpx.AsyncClient(timeout=timeout_s, headers={"User-Agent": user_agent, "Accept": "application/json"})

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = time.time()
            wait = self._min_interval - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.time()

    async def get_json(self, path: str, params: Dict[str, str]) -> Dict[str, Any]:
        await self._throttle()
        url = f"{LB_BASE}{path}"
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        return r.json() if r.content else {}


_lb_client: Optional[ListenBrainzClient] = None


def _client() -> ListenBrainzClient:
    global _lb_client
    if _lb_client is None:
        _lb_client = ListenBrainzClient(user_agent="Helix/0.0.18 (station-discovery)")
    return _lb_client


# Cache raw LB radio responses; these are large but reduce upstream calls a lot.
_lb_radio_cache: TTLCache[Dict[str, Any]] = TTLCache(max_items=512)


def _cache_key(prefix: str, seed: str, mode: str, pop_begin: int, pop_end: int, max_sim: int, max_rec: int) -> str:
    return f"{prefix}:{seed}:{mode}:{pop_begin}:{pop_end}:{max_sim}:{max_rec}"


async def lb_radio_for_artist(
    seed_artist_mbid: str,
    *,
    mode: str = "medium",
    max_similar_artists: int = 200,
    max_recordings_per_artist: int = 50,
    pop_begin: int = 0,
    pop_end: int = 100,
    cache_ttl_s: int = 7 * 24 * 3600,
) -> Dict[str, Any]:
    seed = (seed_artist_mbid or "").strip()
    if not seed:
        return {}
    mode = (mode or "medium").strip().lower()
    if mode not in {"easy", "medium", "hard"}:
        mode = "medium"

    key = _cache_key("lb_radio_artist", seed, mode, int(pop_begin), int(pop_end), int(max_similar_artists), int(max_recordings_per_artist))
    hit = _lb_radio_cache.get(key)
    if hit is not None:
        return hit

    params = {
        "mode": mode,
        "max_similar_artists": str(int(max_similar_artists)),
        "max_recordings_per_artist": str(int(max_recordings_per_artist)),
        "pop_begin": str(int(pop_begin)),
        "pop_end": str(int(pop_end)),
    }
    data = await _client().get_json(f"/1/lb-radio/artist/{seed}", params=params)
    _lb_radio_cache.set(key, data, ttl_seconds=cache_ttl_s)
    return data


async def lb_radio_for_tags(
    tags: List[str],
    *,
    operator: str = "OR",
    count: int = 250,
    pop_begin: int = 0,
    pop_end: int = 100,
    cache_ttl_s: int = 2 * 24 * 3600,
) -> Dict[str, Any]:
    tag_list = [t.strip() for t in (tags or []) if (t or "").strip()]
    if not tag_list:
        return {}

    operator = (operator or "OR").strip().upper()
    if operator not in {"AND", "OR"}:
        operator = "OR"

    # tags order should not matter
    seed = ",".join(sorted(set(tag_list)))
    key = _cache_key("lb_radio_tags", seed, operator.lower(), int(pop_begin), int(pop_end), int(count), 0)
    hit = _lb_radio_cache.get(key)
    if hit is not None:
        return hit

    # ListenBrainz accepts repeated tag params; httpx allows list values.
    params: Dict[str, Any] = {
        "tag": tag_list,
        "operator": operator,
        "count": str(int(count)),
        "pop_begin": str(int(pop_begin)),
        "pop_end": str(int(pop_end)),
    }
    data = await _client().get_json("/1/lb-radio/tags", params=params)  # type: ignore[arg-type]
    _lb_radio_cache.set(key, data, ttl_seconds=cache_ttl_s)
    return data
