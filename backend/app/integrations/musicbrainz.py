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
        _mb_client = MusicBrainzClient(user_agent="Helix/0.0.20 (station-discovery)", min_interval_ms=250, timeout_s=12)
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


_artist_resolution_cache: TTLCache[Dict[str, Any]] = TTLCache(max_items=4096)


def _norm_title(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _dedupe_norm_titles(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        n = _norm_title(v)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(v)
    return out


async def resolve_artist_mbid_from_yt(
    *,
    artist_name: str,
    album_titles: Optional[list[str]] = None,
    song_titles: Optional[list[str]] = None,
    cache_ttl_s: int = 30 * 24 * 3600,
) -> Dict[str, Any]:
    """Resolve a YT artist to a MusicBrainz artist as best we can.

    We intentionally do this lazily (artist-page entry time), not during raw search.
    The most reliable signal is album-overlap rather than name-only matching.
    """
    name = _clean(artist_name)
    album_titles = _dedupe_norm_titles(album_titles or [])
    song_titles = _dedupe_norm_titles(song_titles or [])
    cache_key = f"artist_resolve:{name.lower()}|{'|'.join(_norm_title(a) for a in album_titles[:5])}|{'|'.join(_norm_title(s) for s in song_titles[:5])}"
    cached = _artist_resolution_cache.get(cache_key)
    if cached is not None:
        return cached

    if not name:
        payload = {
            "mb_artist_id": "",
            "mb_resolution_status": "unresolved",
            "mb_resolution_confidence": 0.0,
            "canonical_name": "",
            "artist_type": "",
            "country": "",
            "disambiguation": "",
            "matched_albums": [],
        }
        _artist_resolution_cache.set(cache_key, payload, ttl_seconds=cache_ttl_s)
        return payload

    data = await _client().search("artist", name, limit=10)
    candidates = [a for a in (data.get("artists") or []) if isinstance(a, dict)]
    norm_name = _norm_title(name)

    async def _score_candidate(c: Dict[str, Any]) -> Dict[str, Any]:
        c_name = _clean(str(c.get("name") or ""))
        c_norm = _norm_title(c_name)
        score = 0.0
        if c_norm == norm_name:
            score += 50.0
        elif c_norm.startswith(norm_name) or norm_name.startswith(c_norm):
            score += 35.0
        elif norm_name in c_norm or c_norm in norm_name:
            score += 20.0

        disamb = _clean(str(c.get("disambiguation") or ""))
        disamb_norm = _norm_title(disamb)
        if any(bad in disamb_norm for bad in ("tribute", "karaoke", "cover", "tribute band")):
            score -= 25.0

        c_type = _clean(str(c.get("type") or ""))
        if c_type.lower() in {"group", "person", "orchestra", "choir"}:
            score += 2.0

        matched_albums: list[str] = []
        if album_titles:
            try:
                full = await _client().lookup("artist", str(c.get("id") or ""), inc="release-groups")
            except Exception:
                full = {}
            release_groups = [rg for rg in (full.get("release-groups") or []) if isinstance(rg, dict)]
            rg_titles = {_norm_title(str(rg.get("title") or "")): str(rg.get("title") or "") for rg in release_groups}
            for album in album_titles[:8]:
                n = _norm_title(album)
                if not n:
                    continue
                if n in rg_titles:
                    matched_albums.append(rg_titles[n])
                    score += 22.0
                else:
                    # Partial title overlap can still be meaningful for editions / deluxe versions.
                    for rg_norm, rg_title in rg_titles.items():
                        if not rg_norm:
                            continue
                        if n in rg_norm or rg_norm in n:
                            matched_albums.append(rg_title)
                            score += 12.0
                            break

        confidence = max(0.0, min(0.99, score / 100.0))
        return {
            "id": str(c.get("id") or ""),
            "canonical_name": c_name,
            "artist_type": c_type,
            "country": _clean(str(c.get("country") or "")),
            "disambiguation": disamb,
            "matched_albums": matched_albums,
            "score": score,
            "confidence": confidence,
        }

    scored = [await _score_candidate(c) for c in candidates]
    scored.sort(key=lambda x: x.get("score") or 0.0, reverse=True)
    best = scored[0] if scored else None
    second = scored[1] if len(scored) > 1 else None

    payload = {
        "mb_artist_id": "",
        "mb_resolution_status": "unresolved",
        "mb_resolution_confidence": 0.0,
        "canonical_name": "",
        "artist_type": "",
        "country": "",
        "disambiguation": "",
        "matched_albums": [],
    }
    if best:
        payload.update(
            {
                "mb_artist_id": best.get("id") or "",
                "mb_resolution_confidence": float(best.get("confidence") or 0.0),
                "canonical_name": str(best.get("canonical_name") or ""),
                "artist_type": str(best.get("artist_type") or ""),
                "country": str(best.get("country") or ""),
                "disambiguation": str(best.get("disambiguation") or ""),
                "matched_albums": list(best.get("matched_albums") or []),
            }
        )
        gap = (best.get("score") or 0.0) - (second.get("score") or 0.0 if second else -999.0)
        if (best.get("score") or 0.0) >= 55.0 and gap >= 8.0:
            payload["mb_resolution_status"] = "resolved"
        elif (best.get("score") or 0.0) >= 35.0:
            payload["mb_resolution_status"] = "ambiguous"
        else:
            payload["mb_artist_id"] = ""
            payload["mb_resolution_status"] = "unresolved"
            payload["mb_resolution_confidence"] = 0.0
            payload["canonical_name"] = ""
            payload["artist_type"] = ""
            payload["country"] = ""
            payload["disambiguation"] = ""
            payload["matched_albums"] = []

    _artist_resolution_cache.set(cache_key, payload, ttl_seconds=cache_ttl_s)
    return payload
