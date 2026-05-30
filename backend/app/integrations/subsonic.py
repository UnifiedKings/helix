from __future__ import annotations

import asyncio
import time
import hashlib
import os
import random
import string
from typing import Any, Dict, Optional, Tuple, List

import httpx
import re


def _rand_salt(n: int = 12) -> str:
    return "".join(random.choice(string.ascii_letters + string.digits) for _ in range(n))


def _token(password: str, salt: str) -> str:
    # token auth: md5(password + salt)
    return hashlib.md5((password + salt).encode("utf-8")).hexdigest()


def _norm(s: str) -> str:
    """Normalize for fuzzy matching.

    - lowercase + collapse whitespace
    - normalize common punctuation variants
    - strip apostrophes so Lion's == Lions
    - replace remaining punctuation with spaces
    - keep only [a-z0-9 ] after normalization
    """
    s = (s or "").strip().lower()
    s = s.replace("’", "'").replace("`", "'").replace("´", "'")
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace("'", "")  # lion's -> lions
    s = re.sub(r"[^0-9a-z\s]+", " ", s)
    return " ".join(s.split())
def _contains_bad_variant(title: str) -> bool:
    t = _norm(title)
    bad = [" live", "(live", " session", "radio", "demo", "acoustic", "remix", "mix", "cover", "karaoke"]
    return any(b in t for b in bad)


def _artist_match_quality(want_artist: str, candidate_artist: str) -> float:
    want = _norm(want_artist)
    cand = _norm(candidate_artist)
    if not want or not cand:
        return 0.0
    if cand == want:
        return 1.0
    want_parts = set(want.split())
    cand_parts = set(cand.split())
    if not want_parts or not cand_parts:
        return 0.0
    overlap = len(want_parts & cand_parts) / max(1, len(want_parts | cand_parts))
    # Avoid extremely loose substring matches like soundtrack/team/various-artist
    # metadata matching the requested title but not the requested performer.
    if overlap >= 0.67:
        return overlap
    if want in cand or cand in want:
        shorter = min(len(want), len(cand))
        longer = max(len(want), len(cand))
        if shorter >= 6 and (shorter / max(1, longer)) >= 0.72:
            return 0.55
    return overlap


def _album_candidate_score(album: str, artist: str, candidate: Dict[str, Any]) -> float:
    """Score a Subsonic album candidate by normalized title/artist match quality."""
    nalb = _norm(album)
    na = _norm(artist)
    at_raw = str(candidate.get("title") or candidate.get("name") or "")
    ar_raw = str(candidate.get("artist") or "")
    at = _norm(at_raw)
    ar = _norm(ar_raw)

    title_match = (at == nalb) or (nalb in at) or (at in nalb)
    if not title_match:
        return float("-inf")

    score = 0.0
    score += 100 if at == nalb else 60
    if ar == na:
        score += 80
    elif na and (na in ar or ar in na):
        score += 40
    else:
        score -= 50

    # Prefer candidates with a track count, which are easier to validate downstream.
    try:
        if int(candidate.get("songCount") or 0) > 0:
            score += 5
    except Exception:
        pass

    return score


class SubsonicClient:
    def __init__(self, base_url: str, username: str, password: str, client_name: str = "Helix", api_version: str = "1.16.1", timeout_s: int = 20):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.client_name = client_name
        self.api_version = api_version
        self.timeout = timeout_s
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def close(self):
        await self._http.aclose()

    async def search3(self, query: str) -> Dict[str, Any]:
        """Run Subsonic search3 and return the raw searchResult3 payload."""
        q = (query or "").strip()
        if not q:
            return {}
        url = f"{self.base_url}/rest/search3.view"
        params = {"query": q, **self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {}) or {}
        return data.get("searchResult3", {}) or {}

    def _auth_params(self) -> Dict[str, str]:
        salt = _rand_salt()
        return {
            "u": self.username,
            "t": _token(self.password, salt),
            "s": salt,
            "v": self.api_version,
            "c": self.client_name,
            "f": "json",
        }

    async def search_song_best(self, title: str, artist: str, duration_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Search Subsonic for the best matching song. Returns song dict (Subsonic JSON) or None."""
        q = f'{title} {artist}'.strip()
        url = f"{self.base_url}/rest/search3.view"
        params = {"query": q, **self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {})
        res = data.get("searchResult3", {}) or {}
        songs: List[Dict[str, Any]] = res.get("song") or []
        if not songs:
            return None

        # score candidates
        nt = _norm(title)
        na = _norm(artist)
        best = None
        best_score = -1e9

        for s in songs:
            st_raw = (s.get("title") or "")
            sa_raw = (s.get("artist") or "")
            st = _norm(st_raw)
            sa = _norm(sa_raw)

            title_match = (st == nt) or (nt in st) or (st in nt)
            if not title_match:
                continue

            score = 0.0
            score += 100 if st == nt else 60

            artist_quality = _artist_match_quality(artist, sa_raw)
            if artist_quality >= 0.98:
                score += 80
            elif artist_quality >= 0.55:
                score += 40 * artist_quality
            else:
                # Title-only matches are dangerous for station fulfillment: they
                # can make Helix label and play the wrong song.
                continue

            if _contains_bad_variant(st_raw):
                score -= 25

            if duration_ms and s.get("duration"):
                ds = int(s.get("duration")) * 1000
                diff = abs(ds - int(duration_ms))
                if diff <= 3000:
                    score += 25
                elif diff <= 8000:
                    score += 10
                elif diff <= 15000:
                    score += 0
                else:
                    score -= 25

            if score > best_score:
                best_score = score
                best = s

        if best is not None:
            best["_helix_match_score"] = best_score
        return best


    async def search_album_candidates(self, album: str, artist: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Return album candidates sorted by normalized title/artist match strength."""
        q = f"{album} {artist}".strip()
        url = f"{self.base_url}/rest/search3.view"
        params = {"query": q, **self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {})
        res = data.get("searchResult3", {}) or {}
        albums: List[Dict[str, Any]] = res.get("album") or []
        if not albums:
            return []

        scored: List[tuple[float, Dict[str, Any]]] = []
        seen_ids = set()
        for a in albums:
            aid = str(a.get("id") or "").strip()
            if aid and aid in seen_ids:
                continue
            if aid:
                seen_ids.add(aid)

            score = _album_candidate_score(album, artist, a)
            if score == float("-inf"):
                continue
            scored.append((score, a))

        scored.sort(key=lambda item: item[0], reverse=True)
        return [a for _, a in scored[: max(1, int(limit))]]

    async def search_album_best(self, album: str, artist: str) -> Optional[Dict[str, Any]]:
        """Search Subsonic for the best matching album. Returns album dict (Subsonic JSON) or None."""
        candidates = await self.search_album_candidates(album=album, artist=artist, limit=1)
        return candidates[0] if candidates else None


    async def get_album_songs(self, album_id: str) -> List[Dict[str, Any]]:
        """Fetch album tracklist via getAlbum.view. Returns a list of song dicts."""
        if not album_id:
            return []
        url = f"{self.base_url}/rest/getAlbum.view"
        params = {"id": album_id, **self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {})
        album = data.get("album", {}) or {}
        songs = album.get("song") or []
        if isinstance(songs, list):
            return songs
        return []


    def stream_url(self, song_id: str) -> str:
        url = f"{self.base_url}/rest/stream.view"
        # We intentionally do NOT include password; use token auth.
        # Client will fetch through Helix proxy endpoint, so this is mostly for debugging.
        return url + f"?id={httpx.QueryParams({'id': song_id}).get('id')}"

    async def start_scan(self) -> bool:
        """Trigger a media scan (Navidrome supports this through Subsonic API)."""
        url = f"{self.base_url}/rest/startScan.view"
        params = {**self._auth_params()}
        try:
            r = await self._http.get(url, params=params)
            r.raise_for_status()
            return True
        except Exception:
            return False

    async def get_song(self, song_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a song by id (best-effort)."""
        if not song_id:
            return None
        url = f"{self.base_url}/rest/getSong.view"
        params = {"id": song_id, **self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {}) or {}
        return data.get("song")

    async def search_albums_by_artist(self, artist: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Best-effort: return a list of albums for the given artist name.

        We use Subsonic's search3 endpoint because it's widely supported by Subsonic-compatible
        servers (including Navidrome). Results are filtered to match the artist name (normalized).
        """
        artist = (artist or "").strip()
        if not artist:
            return []
        url = f"{self.base_url}/rest/search3.view"
        params = {"query": artist, **self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {})
        res = data.get("searchResult3", {}) or {}
        albums: List[Dict[str, Any]] = res.get("album") or []
        if not albums:
            return []

        na = _norm(artist)
        out: List[Dict[str, Any]] = []
        seen = set()
        for a in albums:
            # Prefer albums whose artist matches.
            aa = _norm(str(a.get("artist") or ""))
            if na and aa and aa != na:
                continue
            cover = str(a.get("coverArt") or "").strip()
            if not cover:
                continue
            aid = str(a.get("id") or "").strip()
            if aid and aid in seen:
                continue
            if aid:
                seen.add(aid)
            out.append(a)
            if len(out) >= int(limit):
                break
        return out

    async def fetch_cover_art_bytes(self, cover_id: str, *, size: int = 512) -> Optional[bytes]:
        """Fetch cover art bytes by cover id. Returns None on failure."""
        cover_id = (cover_id or "").strip()
        if not cover_id:
            return None
        url = f"{self.base_url}/rest/getCoverArt.view"
        params: Dict[str, Any] = {"id": cover_id, **self._auth_params()}
        # Many servers support 'size' for resizing. It's safe to try.
        if size:
            params["size"] = int(size)
        try:
            r = await self._http.get(url, params=params)
            r.raise_for_status()
            return r.content
        except Exception:
            return None

    async def wait_for_song_best(
        self,
        title: str,
        artist: str,
        duration_ms: Optional[int] = None,
        timeout_s: int = 45,
        poll_s: float = 2.0,
    ) -> Optional[Dict[str, Any]]:
        """Poll Subsonic until a best-match song appears or timeout."""
        end = time.time() + max(1, int(timeout_s))
        # quick initial try
        try:
            s = await self.search_song_best(title=title, artist=artist, duration_ms=duration_ms)
            if s and s.get("id"):
                return s
        except Exception:
            pass

        while time.time() < end:
            await asyncio.sleep(max(0.25, float(poll_s)))
            try:
                s = await self.search_song_best(title=title, artist=artist, duration_ms=duration_ms)
                if s and s.get("id"):
                    return s
            except Exception:
                continue
        return None
