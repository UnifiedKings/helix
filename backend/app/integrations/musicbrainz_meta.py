from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from ..cache import TTLCache
from .musicbrainz import MusicBrainzClient


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
        _mb_client = MusicBrainzClient(user_agent="Helix/0.0.18 (station-discovery)", min_interval_ms=1100, timeout_s=20)
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
