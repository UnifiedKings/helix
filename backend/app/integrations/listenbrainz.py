from __future__ import annotations

import asyncio
import os
import time
import random
import socket
from typing import Any, Dict, List, Optional, Union

import urllib
import json
import httpx

from ..cache import TTLCache


# Simple in-memory TTL cache
_lb_cache: Dict[str, Any] = {}
_lb_cache_expiry: Dict[str, float] = {}
LB_CACHE_TTL = 3600  # 1 hour

LB_BASE = "https://api.listenbrainz.org"

def _cache_get(key: str):
    if key in _lb_cache and time.time() < _lb_cache_expiry.get(key, 0):
        return _lb_cache[key]
    return None


def _cache_set(key: str, value):
    _lb_cache[key] = value
    _lb_cache_expiry[key] = time.time() + LB_CACHE_TTL


async def _lb_get(session: aiohttp.ClientSession, url: str, params=None):
    async with session.get(url, params=params) as resp:
        if resp.status != 200:
            raise RuntimeError(f"ListenBrainz API error {resp.status}")
        return await resp.json()
def _now_s() -> float:
    return time.time()


def _safe_snip(b: bytes, limit: int = 300) -> str:
    try:
        s = b.decode("utf-8", errors="replace")
    except Exception:
        return ""
    s = s.replace("\n", " ").replace("\r", " ")
    return s[:limit]


class ListenBrainzClient:
    """Thin async client with basic politeness throttling + auth + rate-limit handling.

    Notes:
      - LB Radio endpoints now require an Authorization header (Token ...). :contentReference[oaicite:6]{index=6}
      - ListenBrainz uses X-RateLimit-* headers. :contentReference[oaicite:7]{index=7}
    """

    def __init__(
        self,
        user_agent: str,
        *,
        token: str = "",
        min_interval_ms: int = 250,
        timeout_s: int = 20,
        max_retries: int = 2,
    ):
        self._user_agent = user_agent
        self._token = (token or "").strip()
        self._min_interval = max(0.0, float(min_interval_ms) / 1000.0)
        self._timeout = timeout_s
        self._max_retries = max(0, int(max_retries))

        self._lock = asyncio.Lock()
        self._last_request_at = 0.0

        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json",
        }
        if self._token:
            # ListenBrainz expects: Authorization: Token <user-token>
            # :contentReference[oaicite:8]{index=8}
            headers["Authorization"] = f"Token {self._token}"

        self._client = httpx.AsyncClient(timeout=timeout_s, headers=headers)

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._lock:
            now = _now_s()
            wait = self._min_interval - (now - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = _now_s()

    @staticmethod
    def _compute_backoff_seconds(r: httpx.Response) -> float:
        # Prefer Retry-After, then X-RateLimit-Reset (epoch seconds)
        ra = (r.headers.get("Retry-After") or "").strip()
        if ra:
            try:
                return max(0.0, float(ra))
            except Exception:
                pass

        reset = (r.headers.get("X-RateLimit-Reset") or "").strip()
        if reset:
            try:
                reset_epoch = float(reset)
                return max(0.0, reset_epoch - _now_s())
            except Exception:
                pass

        # fallback: small backoff
        return 1.0

    async def get_json(
        self,
        path: str,
        params: Dict[str, Union[str, int, List[str]]],
        *,
        require_auth: bool = False,
    ) -> Dict[str, Any]:
        # If caller says auth is required but we have no token, fail early with a clear error.
        if require_auth and not self._token:
            raise RuntimeError(
                "ListenBrainz auth token is required for this endpoint. "
                "Set LISTENBRAINZ_TOKEN in the environment."
            )

        await self._throttle()
        url = f"{LB_BASE}{path}"

        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                r = await self._client.get(url, params=params)

                if r.status_code in (429, 503):
                    # Rate limit / temporary overload
                    wait_s = self._compute_backoff_seconds(r)
                    if attempt < self._max_retries:
                        await asyncio.sleep(wait_s)
                        continue

                if 400 <= r.status_code:
                    # Log enough to understand what's happening (HTML/Anubis, auth, etc.)
                    snip = _safe_snip(r.content)
                    # We raise with a message that includes status and snippet.
                    raise httpx.HTTPStatusError(
                        f"ListenBrainz {r.status_code} for {path} params={params} body='{snip}'",
                        request=r.request,
                        response=r,
                    )

                return r.json() if r.content else {}

            except Exception as e:
                last_exc = e
                if attempt < self._max_retries:
                    # small exponential-ish backoff
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                break

        # Exhausted retries
        if last_exc:
            raise last_exc
        return {}


_lb_client: Optional[ListenBrainzClient] = None


def _client() -> ListenBrainzClient:
    global _lb_client
    if _lb_client is None:
        token = os.getenv("LISTENBRAINZ_TOKEN", "").strip()
        _lb_client = ListenBrainzClient(
            user_agent="Helix/0.0.18 (contact@aidanbrennan.dev)",
            token=token,
            min_interval_ms=250,
            timeout_s=20,
            max_retries=2,
        )
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

    params: Dict[str, Union[str, int, List[str]]] = {
        "mode": mode,
        "max_similar_artists": str(int(max_similar_artists)),
        "max_recordings_per_artist": str(int(max_recordings_per_artist)),
        "pop_begin": str(int(pop_begin)),
        "pop_end": str(int(pop_end)),
    }

    # LB Radio requires auth now. :contentReference[oaicite:9]{index=9}
    data = await _client().get_json(f"/1/lb-radio/artist/{seed}", params=params, require_auth=True)
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
    params: Dict[str, Union[str, int, List[str]]] = {
        "tag": tag_list,
        "operator": operator,
        "count": str(int(count)),
        "pop_begin": str(int(pop_begin)),
        "pop_end": str(int(pop_end)),
    }

    # Treat tags radio as requiring auth too to match current LB Radio policy. :contentReference[oaicite:10]{index=10}
    data = await _client().get_json("/1/lb-radio/tags", params=params, require_auth=True)
    _lb_radio_cache.set(key, data, ttl_seconds=cache_ttl_s)
    return data

async def lb_similar_artists_for_artist(
    seed_artist_mbid: str,
    limit: int = 30,
    *,
    # Dataset-hoster algorithm identifier. The hoster exposes multiple variants.
    # This default is a good "general purpose" one and can be made configurable later.
    algorithm: str = "session_based_days_7500_session_300_contribution_5_threshold_10_limit_100_filter_True_skip_30",
) -> List[Dict[str, Any]]:
    """
    Fetch a ranked similar-artists list from the ListenBrainz dataset hoster.

    Uses:
      GET https://labs.api.listenbrainz.org/similar-artists/json?artist_mbids=<MBID>&algorithm=<ALGO>

    Returns (ranked):
      [{"artist_mbid": "...", "artist_name": "...", "score": 1234}, ...] up to `limit`
    """

    mbid = (seed_artist_mbid or "").strip().strip("+").strip()
    if not mbid:
        return []

    algo = (algorithm or "").strip()
    cache_key = f"similar_artists_ranked:{mbid}:{limit}:{algo}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    params = {
        "artist_mbids": mbid,
        "algorithm": algo,
    }
    url = f"https://labs.api.listenbrainz.org/similar-artists/json?{urllib.parse.urlencode(params)}"

    def _fetch_json(u: str) -> Any:
        req = urllib.request.Request(
            u,
            headers={
                "Accept": "application/json",
                "User-Agent": "Helix/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            raise RuntimeError(f"HTTP {e.code} {e.reason} body={err_body} url={u}") from e

    data = await asyncio.to_thread(_fetch_json, url)

    # Dataset hoster commonly returns either:
    #  - a list of row dicts
    #  - or a dict containing one or more lists
    rows: List[Dict[str, Any]] = []
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
    elif isinstance(data, dict):
        if isinstance(data.get("similar_artists"), list):
            rows = [r for r in (data.get("similar_artists") or []) if isinstance(r, dict)]
        elif isinstance(data.get("data"), list):
            rows = [r for r in (data.get("data") or []) if isinstance(r, dict)]
        else:
            # best-effort: flatten any list values
            for v in data.values():
                if isinstance(v, list):
                    rows.extend([r for r in v if isinstance(r, dict)])

    results: List[Dict[str, Any]] = []
    seen = set()
    for r in rows:
        # Skip the reference artist row(s) which typically have no score.
        score = r.get("score")
        if score is None:
            continue

        sim_mbid = (r.get("artist_mbid") or "").strip().strip("+").strip()
        sim_name = (r.get("name") or r.get("artist_name") or "").strip()
        if not sim_mbid or not sim_name:
            continue
        if sim_mbid in seen:
            continue
        seen.add(sim_mbid)
        try:
            score_i = int(score)
        except Exception:
            score_i = None
        results.append({"artist_mbid": sim_mbid, "artist_name": sim_name, "score": score_i})

    # Ensure most-similar first (highest score). If API is already ordered, this is a no-op.
    results.sort(key=lambda x: (x.get("score") is None, -(x.get("score") or 0)))
    results = results[: max(0, int(limit))]
    _cache_set(cache_key, results)
    return results

async def lb_top_recordings_for_artist(
    artist: Union[str, Dict[str, Any]],
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """
    Uses:
      GET https://api.listenbrainz.org/1/popularity/top-recordings-for-artist/{artist_mbid}

    No aiohttp: urllib in a thread.
    """

    # Normalize input
    if isinstance(artist, dict):
        artist_mbid = (artist.get("artist_mbid") or "").strip().strip("+").strip()
    else:
        artist_mbid = (artist or "").strip().strip("+").strip()

    if not artist_mbid:
        return []

    cache_key = f"top_recordings:{artist_mbid}:{limit}"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    # FORCE versioned endpoint to avoid 410 Gone
    url = f"https://api.listenbrainz.org/1/popularity/top-recordings-for-artist/{artist_mbid}"

    def _fetch_json(u: str) -> Dict[str, Any]:
        req = urllib.request.Request(
            u,
            headers={
                "Accept": "application/json",
                "User-Agent": "Helix/1.0",
            },
            method="GET",
        )

        # Retry transient network failures (connection reset, timeouts, brief 5xx).
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                # Retry some server-side errors and rate limiting.
                if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                    last_exc = e
                else:
                    try:
                        err_body = e.read().decode("utf-8", errors="replace")
                    except Exception:
                        err_body = ""
                    raise RuntimeError(f"HTTP {e.code} {e.reason} body={err_body} url={u}") from e
            except (ConnectionResetError, socket.timeout, TimeoutError, OSError, urllib.error.URLError) as e:
                last_exc = e

            if attempt < 4:
                # backoff + jitter
                base = 0.35 * (2 ** attempt)
                time.sleep(base + random.random() * 0.2)

        raise RuntimeError(f"network error after retries: {last_exc}") from last_exc

    try:
        data = await asyncio.to_thread(_fetch_json, url)
    except Exception as e:
        # Include url so you can confirm instantly what is being hit
        raise RuntimeError(f"ListenBrainz popularity request failed: {e}") from e

    recordings = []
    if isinstance(data, dict):
        recordings = (data.get("payload") or {}).get("recordings") or []
    elif isinstance(data, list):
        recordings = data
    else:
        recordings = []


    results: List[Dict[str, Any]] = []
    for rec in recordings[: max(0, limit)]:
        results.append({
            "recording_mbid": rec.get("recording_mbid"),
            "recording_name": rec.get("recording_name"),
            "artist_name": rec.get("artist_name"),
            "listen_count": rec.get("listen_count"),
        })
    print(results)
    _cache_set(cache_key, results)
    return results