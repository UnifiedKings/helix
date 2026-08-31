from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

LRCLIB_BASE_URL = "https://lrclib.net"
LRCLIB_TIMEOUT_S = float(os.getenv("HELIX_LRCLIB_TIMEOUT_S", "8"))
SUCCESS_TTL_S = int(os.getenv("HELIX_LYRICS_CACHE_TTL_S", str(30 * 24 * 60 * 60)))
MISS_TTL_S = int(os.getenv("HELIX_LYRICS_MISS_CACHE_TTL_S", str(24 * 60 * 60)))
CACHE_DIR = Path(os.getenv("HELIX_LYRICS_CACHE_DIR", "/data/helix/lyrics_cache"))
USER_AGENT = os.getenv(
    "HELIX_LRCLIB_USER_AGENT",
    "Helix/0.1.0 (https://github.com/UnifiedKings/helix)",
)

_LRC_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_cache_lock = asyncio.Lock()
_request_lock = asyncio.Lock()
_last_request_at = 0.0
_retry_after_until = 0.0


@dataclass(frozen=True)
class LyricsQuery:
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0

    def normalized(self) -> dict[str, Any]:
        return {
            "title": self.title.strip(),
            "artist": self.artist.strip(),
            "album": self.album.strip(),
            "duration_ms": max(0, int(self.duration_ms or 0)),
        }


def _cache_path(query: LyricsQuery) -> Path:
    payload = json.dumps(query.normalized(), sort_keys=True, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def _timestamp_ms(minutes: str, seconds: str, fraction: str | None) -> int:
    ms = (int(minutes) * 60 + int(seconds)) * 1000
    if not fraction:
        return ms
    if len(fraction) == 1:
        ms += int(fraction) * 100
    elif len(fraction) == 2:
        ms += int(fraction) * 10
    else:
        ms += int(fraction[:3].ljust(3, "0"))
    return ms


def parse_synced_lyrics(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []

    lines: list[dict[str, Any]] = []
    for source_line in raw.splitlines():
        matches = list(_LRC_TIMESTAMP.finditer(source_line))
        if not matches:
            continue
        text = _LRC_TIMESTAMP.sub("", source_line).strip()
        for match in matches:
            lines.append(
                {
                    "time_ms": _timestamp_ms(match.group(1), match.group(2), match.group(3)),
                    "text": text,
                }
            )

    lines.sort(key=lambda line: line["time_ms"])
    return lines


def _plain_from_synced(lines: list[dict[str, Any]]) -> str:
    return "\n".join(line["text"] for line in lines if line.get("text")).strip()


def _normalize_lrclib(payload: dict[str, Any], query: LyricsQuery) -> dict[str, Any]:
    synced_raw = payload.get("syncedLyrics") or ""
    lines = parse_synced_lyrics(synced_raw)
    plain = (payload.get("plainLyrics") or "").strip()
    if not plain and lines:
        plain = _plain_from_synced(lines)

    return {
        "found": True,
        "instrumental": bool(payload.get("instrumental")),
        "plain_lyrics": plain,
        "synced_lyrics": synced_raw,
        "lines": lines,
        "source": "lrclib",
        "source_id": payload.get("id"),
        "matched": {
            "track_name": payload.get("trackName") or payload.get("name") or query.title,
            "artist_name": payload.get("artistName") or query.artist,
            "album_name": payload.get("albumName") or query.album,
            "duration_ms": int(round(float(payload.get("duration") or 0) * 1000)),
        },
    }


def _not_found() -> dict[str, Any]:
    return {
        "found": False,
        "instrumental": False,
        "plain_lyrics": "",
        "synced_lyrics": "",
        "lines": [],
        "source": "lrclib",
        "source_id": None,
        "matched": None,
    }


async def _read_cache(query: LyricsQuery) -> dict[str, Any] | None:
    path = _cache_path(query)
    try:
        stat = path.stat()
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None

    ttl = SUCCESS_TTL_S if data.get("found") else MISS_TTL_S
    if ttl > 0 and (time.time() - stat.st_mtime) > ttl:
        try:
            path.unlink()
        except OSError:
            pass
        return None

    data["cached"] = True
    return data


async def _write_cache(query: LyricsQuery, data: dict[str, Any]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _cache_path(query)
        temp = path.with_suffix(".tmp")
        payload = dict(data)
        payload.pop("cached", None)
        temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp.replace(path)
    except OSError:
        # Lyrics caching is an optimization. Never make a lyrics lookup fail because
        # the data directory is unavailable or read-only.
        return


async def _request_lrclib(params: dict[str, Any]) -> httpx.Response:
    global _last_request_at, _retry_after_until

    # LRCLIB asks clients to make requests sequentially, space them slightly,
    # and honor Retry-After when rate limited. Keep that policy centralized here.
    async with _request_lock:
        now = time.monotonic()
        wait_for = max(0.0, _retry_after_until - now, 0.25 - (now - _last_request_at))
        if wait_for > 0:
            await asyncio.sleep(wait_for)

        try:
            async with httpx.AsyncClient(
                base_url=LRCLIB_BASE_URL,
                timeout=LRCLIB_TIMEOUT_S,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            ) as client:
                response = await client.get("/api/get", params=params)
        except httpx.HTTPError as exc:
            _last_request_at = time.monotonic()
            raise RuntimeError(f"LRCLIB request failed: {exc}") from exc

        _last_request_at = time.monotonic()
        if response.status_code == 429:
            try:
                retry_after = max(0.0, float(response.headers.get("Retry-After", "0") or 0))
            except ValueError:
                retry_after = 0.0
            _retry_after_until = max(_retry_after_until, _last_request_at + retry_after)
        return response


async def get_lyrics(query: LyricsQuery) -> dict[str, Any]:
    normalized = query.normalized()
    if not normalized["title"] or not normalized["artist"]:
        return _not_found()

    async with _cache_lock:
        cached = await _read_cache(query)
    if cached is not None:
        return cached

    params: dict[str, Any] = {
        "track_name": normalized["title"],
        "artist_name": normalized["artist"],
    }
    if normalized["album"]:
        params["album_name"] = normalized["album"]
    duration_ms = normalized["duration_ms"]
    if 1000 <= duration_ms <= 3_600_000:
        params["duration"] = round(duration_ms / 1000)

    response = await _request_lrclib(params)

    if response.status_code == 404:
        result = _not_found()
        async with _cache_lock:
            await _write_cache(query, result)
        return result

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", "")
        suffix = f" Retry after {retry_after} seconds." if retry_after else ""
        raise RuntimeError(f"LRCLIB rate limit exceeded.{suffix}")

    try:
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(f"LRCLIB returned an invalid response: {exc}") from exc

    result = _normalize_lrclib(payload, query)
    async with _cache_lock:
        await _write_cache(query, result)
    result["cached"] = False
    return result
