from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import httpx


MB_BASE = "https://musicbrainz.org/ws/2"


class MusicBrainzClient:
    """Thin async client with politeness throttling.

    MusicBrainz asks that clients:
      - send a descriptive User-Agent
      - avoid excessive request rates
    """

    def __init__(self, user_agent: str, min_interval_ms: int = 1000, timeout_s: int = 20):
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
        url = f"{MB_BASE}{path}"
        # Always request JSON
        params = dict(params)
        params["fmt"] = "json"
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def search(self, entity: str, query: str, limit: int = 25, offset: int = 0, inc: Optional[str] = None) -> Dict[str, Any]:
        params = {"query": query, "limit": str(limit), "offset": str(offset)}
        if inc:
            params["inc"] = inc
        return await self.get_json(f"/{entity}", params)

    async def lookup(self, entity: str, mbid: str, inc: Optional[str] = None) -> Dict[str, Any]:
        params: Dict[str, str] = {}
        if inc:
            params["inc"] = inc
        return await self.get_json(f"/{entity}/{mbid}", params)

# ----------------------------
# Cached metadata helpers
# (merged from musicbrainz_meta.py)
# ----------------------------

import re
from typing import Any, Dict, Optional, Tuple

from ..cache import TTLCache


_rec_cache: TTLCache[Dict[str, Any]] = TTLCache(max_items=50000)
_artist_cache: TTLCache[str] = TTLCache(max_items=10000)


def _clean(s: str) -> str:
    return " ".join((s or "").strip().split())


def _first_year(date_str: str) -> int:
    m = re.match(r"^(\d{4})", (date_str or "").strip())
    if not m:
        return 0
    try:
        return int(m.group(1))
    except Exception:
        return 0


_mb_client: Optional[MusicBrainzClient] = None


def _client() -> MusicBrainzClient:
    global _mb_client
    if _mb_client is None:
        _mb_client = MusicBrainzClient(user_agent="Helix/0.0.20 (station-discovery)", min_interval_ms=1000, timeout_s=20)
    return _mb_client


async def lookup_artist_mbid_by_name(name: str) -> str:
    q = _clean(name)
    if not q:
        return ""
    key = f"artist_search:{q.lower()}"
    hit = _artist_cache.get(key)
    if hit is not None:
        return hit
    data = await _client().search("artist", q, limit=1)
    artists = data.get("artists") or []
    mbid = str((artists[0] or {}).get("id") or "") if artists else ""
    _artist_cache.set(key, mbid, ttl_seconds=30 * 24 * 3600)
    return mbid


async def lookup_recording(
    recording_mbid: str,
    *,
    cache_ttl_s: int = 30 * 24 * 3600,
) -> Dict[str, Any]:
    rid = (recording_mbid or "").strip()
    if not rid:
        return {}
    key = f"rec:{rid}"
    hit = _rec_cache.get(key)
    if hit is not None:
        return hit

    # Include artists + releases for album/year + cover art lookup.
    data = await _client().lookup("recording", rid, inc="artists")
    _rec_cache.set(key, data, ttl_seconds=cache_ttl_s)
    return data




async def lookup_recording_full(
    recording_mbid: str,
    *,
    cache_ttl_s: int = 30 * 24 * 3600,
) -> Dict[str, Any]:
    """Full recording lookup including releases for album/year + cover art (heavier)."""
    rid = (recording_mbid or "").strip()
    if not rid:
        return {}
    key = f"rec_full:{rid}"
    hit = _rec_cache.get(key)
    if hit is not None:
        return hit
    data = await _client().lookup("recording", rid, inc="artists+releases")
    _rec_cache.set(key, data, ttl_seconds=cache_ttl_s)
    return data

def simplify_recording(rec: Dict[str, Any]) -> Tuple[str, str, str, int, int, str]:
    """Return (title, artist, album, duration_ms, year, release_mbid)."""
    title = _clean(str(rec.get("title") or ""))

    artist = ""
    ac = rec.get("artist-credit") or []
    if ac and isinstance(ac, list):
        a0 = ac[0] or {}
        if isinstance(a0, dict):
            artist = _clean(str((a0.get("artist") or {}).get("name") or a0.get("name") or ""))
        else:
            artist = _clean(str(a0))

    duration_ms = 0
    try:
        duration_ms = int(rec.get("length") or 0)
    except Exception:
        duration_ms = 0

    album = ""
    year = 0
    release_mbid = ""
    releases = rec.get("releases") or []
    if releases and isinstance(releases, list):
        # Pick the earliest dated release if available.
        best = None
        best_year = None
        for r in releases:
            if not isinstance(r, dict):
                continue
            y = _first_year(str(r.get("date") or ""))
            if best is None:
                best = r
                best_year = y if y else None
                continue
            if y and (best_year is None or y < best_year):
                best = r
                best_year = y
        if best and isinstance(best, dict):
            album = _clean(str(best.get("title") or ""))
            release_mbid = _clean(str(best.get("id") or ""))
            if best_year is not None:
                year = int(best_year)

    return title, artist, album, duration_ms, year, release_mbid
