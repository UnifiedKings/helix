from __future__ import annotations

import asyncio
import time
import hashlib
import os
import random
import string
from typing import Any, Dict, Optional, Tuple, List
from collections import OrderedDict

import httpx
import re
import unicodedata
import logging

logger = logging.getLogger("helix.integrations.subsonic")


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
    # NFKC + casefold makes comparisons explicitly Unicode-aware and
    # case-insensitive on both sides (e.g. "Frank Sinatra" == "frank sinatra").
    s = unicodedata.normalize("NFKC", (s or "").strip()).casefold()
    s = s.replace("’", "'").replace("`", "'").replace("´", "'")
    s = s.replace("–", "-").replace("—", "-")
    s = s.replace("'", "")  # lion's -> lions
    s = re.sub(r"[^0-9a-z\s]+", " ", s)
    return " ".join(s.split())


# Short-lived process-wide resolver cache. Several Helix surfaces can ask
# "is this exact track in Subsonic?" at the same time; without deduplication,
# each request expands into multiple search3 queries.
_SONG_RESOLVE_CACHE: "OrderedDict[str, tuple[float, Optional[Dict[str, Any]]]]" = OrderedDict()
_SONG_RESOLVE_INFLIGHT: dict[str, asyncio.Future] = {}
_SONG_RESOLVE_CACHE_MAX = int(os.getenv("HELIX_SUBSONIC_RESOLVE_CACHE_MAX", "1000"))
_SONG_RESOLVE_POSITIVE_TTL_S = float(os.getenv("HELIX_SUBSONIC_RESOLVE_POSITIVE_TTL_S", "60"))
_SONG_RESOLVE_NEGATIVE_TTL_S = float(os.getenv("HELIX_SUBSONIC_RESOLVE_NEGATIVE_TTL_S", "10"))


def _song_resolve_cache_key(
    base_url: str,
    title: str,
    artist: str,
    album: str,
    duration_ms: Optional[int],
) -> str:
    # Duration is intentionally bucketed to the nearest second so tiny metadata
    # differences do not defeat deduplication.
    duration_s = int((int(duration_ms or 0) + 500) / 1000) if duration_ms else 0
    return "|".join([
        (base_url or "").rstrip("/").casefold(),
        _norm(title),
        _norm(artist),
        _norm(album),
        str(duration_s),
    ])


def _song_resolve_cache_get(key: str) -> tuple[bool, Optional[Dict[str, Any]]]:
    row = _SONG_RESOLVE_CACHE.get(key)
    if not row:
        return False, None
    expires_at, value = row
    if time.monotonic() >= expires_at:
        _SONG_RESOLVE_CACHE.pop(key, None)
        return False, None
    _SONG_RESOLVE_CACHE.move_to_end(key)
    return True, value


def _song_resolve_cache_put(key: str, value: Optional[Dict[str, Any]]) -> None:
    ttl = _SONG_RESOLVE_POSITIVE_TTL_S if value else _SONG_RESOLVE_NEGATIVE_TTL_S
    _SONG_RESOLVE_CACHE[key] = (time.monotonic() + max(0.0, ttl), value)
    _SONG_RESOLVE_CACHE.move_to_end(key)
    while len(_SONG_RESOLVE_CACHE) > max(50, _SONG_RESOLVE_CACHE_MAX):
        _SONG_RESOLVE_CACHE.popitem(last=False)


def _strip_common_edition_suffixes(title: str) -> str:
    """Return a title with common release/edition qualifiers removed.

    This is intentionally conservative: it strips qualifiers that commonly
    differ between YT Music and Subsonic tags without collapsing meaningful
    variants such as live, acoustic, remix, demo, or cover recordings.
    """
    value = (title or "").strip()
    if not value:
        return ""

    # Examples:
    #   My Way (2008 Remastered) -> My Way
    #   Song - 2011 Remaster -> Song
    #   Song (Remastered 2017) -> Song
    patterns = [
        r"\s*[\(\[]\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\s*[\)\]]\s*$",
        r"\s*[-–—]\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\s*$",
        r"\s*[\(\[]\s*(?:\d{4}\s+)?(?:album\s+)?version\s*[\)\]]\s*$",
    ]
    previous = None
    while value and value != previous:
        previous = value
        for pattern in patterns:
            value = re.sub(pattern, "", value, flags=re.IGNORECASE).strip()
    return value


def _title_match_quality(want_title: str, candidate_title: str) -> float:
    """Score title identity while tolerating harmless edition-tag drift."""
    want = _norm(want_title)
    cand = _norm(candidate_title)
    if not want or not cand:
        return 0.0
    if want == cand:
        return 1.0

    want_base = _norm(_strip_common_edition_suffixes(want_title))
    cand_base = _norm(_strip_common_edition_suffixes(candidate_title))
    if want_base and cand_base and want_base == cand_base:
        return 0.95

    # Preserve the previous containment behavior for minor punctuation/tag drift.
    if want in cand or cand in want:
        shorter = min(len(want), len(cand))
        longer = max(len(want), len(cand))
        if shorter >= 4 and (shorter / max(1, longer)) >= 0.65:
            return 0.70

    if want_base and cand_base and (want_base in cand_base or cand_base in want_base):
        shorter = min(len(want_base), len(cand_base))
        longer = max(len(want_base), len(cand_base))
        if shorter >= 4 and (shorter / max(1, longer)) >= 0.72:
            return 0.65
    return 0.0


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
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        client_name: str = "Helix",
        api_version: str = "1.16.1",
        timeout_s: int = 20,
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.username = username
        self.password = password
        self.client_name = client_name
        self.api_version = api_version
        self.timeout = timeout_s
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def close(self):
        await self._http.aclose()

    async def search3(self, query: str, song_count: int = 50) -> Dict[str, Any]:
        """Run Subsonic search3 and return the raw searchResult3 payload."""
        q = (query or "").strip()
        if not q:
            return {}
        url = f"{self.base_url}/rest/search3.view"
        params = {"query": q, "songCount": max(1, int(song_count)), **self._auth_params()}
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

    async def search_song_best(
        self,
        title: str,
        artist: str,
        duration_ms: Optional[int] = None,
        album: str = "",
        use_cache: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Search Subsonic for the same artist + track title.

        Library-presence checks should answer a simple question: does Subsonic
        already contain this artist/title pair? Album and duration are deliberately
        not part of the identity decision because they commonly drift between YT
        Music and local tags.

        Common non-musical edition suffixes (for example ``(2008 Remastered)`` or
        ``- 2011 Remaster``) are ignored, while meaningful variants such as live,
        acoustic, remix, demo, and cover remain distinct.
        """
        raw_title = (title or "").strip()
        raw_artist = (artist or "").strip()
        if not raw_title or not raw_artist:
            return None

        cache_key = _song_resolve_cache_key(
            self.base_url,
            raw_title,
            raw_artist,
            album,
            duration_ms,
        )

        if use_cache:
            hit, cached = _song_resolve_cache_get(cache_key)
            if hit:
                return cached

            inflight = _SONG_RESOLVE_INFLIGHT.get(cache_key)
            if inflight is not None:
                try:
                    return await asyncio.shield(inflight)
                except Exception:
                    pass

            loop = asyncio.get_running_loop()
            owner_future = loop.create_future()
            _SONG_RESOLVE_INFLIGHT[cache_key] = owner_future
        else:
            owner_future = None

        result: Optional[Dict[str, Any]] = None
        resolve_error: Exception | None = None

        try:
            result = await self._search_song_best_uncached(
                raw_title,
                raw_artist,
                duration_ms=duration_ms,
                album=album,
            )
            if use_cache:
                _song_resolve_cache_put(cache_key, result)
            return result
        except Exception as exc:
            resolve_error = exc
            raise
        finally:
            if use_cache and owner_future is not None:
                _SONG_RESOLVE_INFLIGHT.pop(cache_key, None)
                if not owner_future.done():
                    if resolve_error is not None:
                        owner_future.set_exception(resolve_error)
                    else:
                        owner_future.set_result(result)

    async def _search_song_best_uncached(
        self,
        raw_title: str,
        raw_artist: str,
        duration_ms: Optional[int] = None,
        album: str = "",
    ) -> Optional[Dict[str, Any]]:
        """Actual Subsonic search implementation; callers normally use search_song_best()."""
        base_title = _strip_common_edition_suffixes(raw_title)
        want_artist = _norm(raw_artist)
        want_title = _norm(raw_title)
        want_base_title = _norm(base_title)

        # Search3 implementations vary in tokenization. Try the precise combined
        # forms first, then title-only forms so Helix can inspect the returned
        # metadata itself instead of depending on the server's ranking.
        queries: List[str] = []
        # Include normalized lowercase/case-folded variants as well as the raw
        # metadata. Some Subsonic-compatible servers tokenize/search differently,
        # so this gives the resolver the best chance to retrieve the candidate
        # before Helix applies its own strict artist+title comparison.
        for q in (
            f"{raw_title} {raw_artist}".strip(),
            f"{base_title} {raw_artist}".strip(),
            f"{want_title} {want_artist}".strip(),
            f"{want_base_title} {want_artist}".strip(),
            raw_title,
            base_title,
            want_title,
            want_base_title,
            raw_artist,
            want_artist,
        ):
            if q and q not in queries:
                queries.append(q)

        songs: List[Dict[str, Any]] = []
        seen_ids = set()
        for q in queries:
            res = await self.search3(q, song_count=100)
            for song in (res.get("song") or []):
                sid = str(song.get("id") or "").strip()
                dedupe_key = sid or f"{_norm(str(song.get('title') or ''))}|{_norm(str(song.get('artist') or ''))}"
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)
                songs.append(song)

        if not songs:
            logger.warning(
                "Subsonic song resolve miss: no candidates title=%r artist=%r normalized_title=%r normalized_artist=%r queries=%r",
                raw_title,
                raw_artist,
                want_base_title or want_title,
                want_artist,
                queries,
            )
            return None

        # Artist identity is intentionally strict after punctuation/case
        # normalization. Title identity is strict after additionally removing only
        # harmless edition/remaster suffixes.
        for song in songs:
            candidate_artist = _norm(str(song.get("artist") or ""))
            if candidate_artist != want_artist:
                continue

            candidate_raw_title = str(song.get("title") or "")
            candidate_title = _norm(candidate_raw_title)
            candidate_base_title = _norm(_strip_common_edition_suffixes(candidate_raw_title))

            if candidate_title == want_title:
                song["_helix_match_score"] = 200.0
                return song

            if want_base_title and candidate_base_title and candidate_base_title == want_base_title:
                song["_helix_match_score"] = 190.0
                return song

        logger.warning(
            "Subsonic song resolve miss: candidates did not match title=%r artist=%r normalized_title=%r normalized_artist=%r queries=%r candidates=%r",
            raw_title,
            raw_artist,
            want_base_title or want_title,
            want_artist,
            queries,
            [
                {
                    "title": str(song.get("title") or ""),
                    "artist": str(song.get("artist") or ""),
                    "normalized_title": _norm(_strip_common_edition_suffixes(str(song.get("title") or ""))),
                    "normalized_artist": _norm(str(song.get("artist") or "")),
                    "id": str(song.get("id") or ""),
                }
                for song in songs[:25]
            ],
        )
        return None

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

    async def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a full album via getAlbum.view, including its track list."""
        if not album_id:
            return None
        url = f"{self.base_url}/rest/getAlbum.view"
        params = {"id": album_id, **self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {}) or {}
        album = data.get("album") or {}
        return album if isinstance(album, dict) and album else None

    async def get_album_songs(self, album_id: str) -> List[Dict[str, Any]]:
        """Fetch album tracklist via getAlbum.view. Returns a list of song dicts."""
        album = await self.get_album(album_id)
        if not album:
            return []
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

    async def get_scan_status(self) -> Dict[str, Any]:
        """Return the current Subsonic/Navidrome media-scan status."""
        url = f"{self.base_url}/rest/getScanStatus.view"
        params = {**self._auth_params()}
        r = await self._http.get(url, params=params)
        r.raise_for_status()
        data = (r.json() or {}).get("subsonic-response", {}) or {}
        status = data.get("scanStatus") or {}
        return status if isinstance(status, dict) else {}

    async def wait_for_scan_complete(
        self,
        timeout_s: float = 120.0,
        poll_s: float = 1.0,
    ) -> bool:
        """Wait for a triggered Navidrome scan to finish."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1.0, float(timeout_s))
        saw_scanning = False

        while loop.time() < deadline:
            status = await self.get_scan_status()
            scanning = bool(status.get("scanning"))

            if scanning:
                saw_scanning = True
            elif saw_scanning:
                return True
            else:
                # Very small libraries may finish before the first status poll.
                await asyncio.sleep(min(max(0.2, float(poll_s)), 1.0))
                status = await self.get_scan_status()
                if not bool(status.get("scanning")):
                    return True

            await asyncio.sleep(max(0.2, float(poll_s)))

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
            # This path intentionally bypasses the negative resolver cache because
            # it is used while waiting for a just-imported track to appear.
            s = await self.search_song_best(
                title=title,
                artist=artist,
                duration_ms=duration_ms,
                use_cache=False,
            )
            if s and s.get("id"):
                return s
        except Exception:
            pass

        while time.time() < end:
            await asyncio.sleep(max(0.25, float(poll_s)))
            try:
                s = await self.search_song_best(
                    title=title,
                    artist=artist,
                    duration_ms=duration_ms,
                    use_cache=False,
                )
                if s and s.get("id"):
                    return s
            except Exception:
                continue
        return None
