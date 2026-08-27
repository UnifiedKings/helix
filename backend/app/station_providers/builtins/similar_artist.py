from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from typing import Any

from ...integrations.ytmusic import find_artist_by_name, get_artist_popular_songs, get_artist_related_artists
from ..base import StationProvider
from ..models import StationConfigOption, StationContext, StationResult

LOG = logging.getLogger("helix.station_providers.similar_artist")


def _clean(s: str) -> str:
    return " ".join((s or "").strip().split())


def _norm(s: str) -> str:
    s = _clean(s).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_artist(s: str) -> str:
    a = _norm(s)
    if not a:
        return ""
    a = re.sub(r",\s*the$", "", a).strip()
    a = re.sub(r"^the\s+", "", a).strip()
    a = re.sub(r"[^a-z0-9\s]+", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    return a


def _norm_pair(title: str, artist: str) -> str:
    return f"{_norm(title)}|{_norm(artist)}"




def _blocked_artists_for_cooldown(
    context: StationContext,
    selected: list[StationResult],
    cooldown: int,
) -> set[str]:
    """Artists appearing in the N tracks immediately preceding the next pick."""
    cooldown = max(0, int(cooldown or 0))
    if cooldown <= 0:
        return set()

    sequence: list[str] = []
    sequence.extend(result.artist for result in reversed(selected))
    sequence.extend(result.artist for result in reversed(context.already_selected or []))
    sequence.extend(item.artist for item in reversed(context.queued_tracks or []))
    sequence.extend(item.artist for item in context.recent_tracks or [])
    return {_norm_artist(artist) for artist in sequence[:cooldown] if _norm_artist(artist)}

def _infer_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _clean_recording_name(item: dict[str, Any]) -> str:
    return _clean(str(item.get("recording_name") or item.get("track_name") or item.get("title") or item.get("name") or ""))


def _clean_artist_name(item: dict[str, Any]) -> str:
    return _clean(str(item.get("similar_artist_name") or item.get("artist_name") or item.get("artist") or ""))


def _pick_station_artist(
    candidate_artists: list[dict[str, Any]],
    *,
    seed_artist: str,
    seed_influence: float,
    discovery: float,
    recent_artists: list[str],
    blacklist: set[str],
    selected_artists: list[str],
    blocked_artists: set[str] | None = None,
) -> str:
    seed_n = _norm_artist(seed_artist or "")
    recent_set = {_norm_artist(a) for a in (recent_artists or [])[:50] if a}
    selected_set = {_norm_artist(a) for a in (selected_artists or []) if a}
    blocked_set = {_norm_artist(a) for a in (blocked_artists or set()) if a}

    parsed: list[tuple[str, int]] = []
    for it in candidate_artists or []:
        name = _clean(str(it.get("similar_artist_name") or it.get("artist_name") or it.get("artist") or ""))
        if not name:
            continue
        na = _norm_artist(name)
        if not na or na in blacklist or na in blocked_set:
            continue
        rank = _infer_int(it.get("rank") or it.get("similar_artist_rank") or 0, 0)
        parsed.append((name, max(0, rank)))

    if not parsed:
        return ""

    discovery = max(0.0, min(1.0, float(discovery)))
    seed_influence = max(0.0, min(1.0, float(seed_influence)))

    weights: list[float] = []
    names: list[str] = []
    for name, rank in parsed:
        na = _norm_artist(name)
        base = 1.0 / float(rank + 1)
        power = 1.8 - (discovery * 1.4)
        weight = base ** power
        if seed_n and na == seed_n:
            weight *= 1.0 + (2.0 * seed_influence)
        if na in recent_set:
            weight *= 0.15
        if na in selected_set:
            weight *= 0.10
        weights.append(max(0.0001, float(weight)))
        names.append(name)

    try:
        return random.choices(names, weights=weights, k=1)[0]
    except Exception:
        return random.choice(names)


class SimilarArtistProvider(StationProvider):
    station_type = "similar_artist"
    display_name = "Similar Artist Radio"
    description = "Uses YouTube Music related artists and artist songs to recommend station tracks."
    version = "1.3.0"
    builtin = True

    def __init__(self) -> None:
        # The registry keeps one provider instance alive, so a short-lived in-memory
        # cache prevents repeated YTMusic lookups while a station fills its queue.
        self._cache: dict[tuple[Any, ...], tuple[float, Any]] = {}

    def _cache_ttl(self) -> float:
        try:
            return max(0.0, float(os.getenv("HELIX_YTMUSIC_STATION_CACHE_TTL_S", "600")))
        except Exception:
            return 600.0

    def _cache_get(self, key: tuple[Any, ...]) -> Any:
        row = self._cache.get(key)
        if not row:
            return None
        expires_at, value = row
        if expires_at <= time.monotonic():
            self._cache.pop(key, None)
            return None
        return value

    def _cache_put(self, key: tuple[Any, ...], value: Any) -> Any:
        ttl = self._cache_ttl()
        if ttl > 0:
            # Bound cache growth for long-running self-hosted instances.
            if len(self._cache) >= 512:
                now = time.monotonic()
                self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
                if len(self._cache) >= 512:
                    self._cache.pop(next(iter(self._cache)), None)
            self._cache[key] = (time.monotonic() + ttl, value)
        return value

    def config_options(self) -> list[StationConfigOption]:
        return [
            StationConfigOption(
                key="seed_artist",
                label="Seed artist",
                type="string",
                description="The artist used as the anchor for YouTube Music related-artist recommendations.",
                required=True,
            ),
            StationConfigOption(
                key="related_artist_limit",
                label="Related artists",
                type="integer",
                description="How many YouTube Music related artists Helix should consider for this station.",
                default=35,
                min_value=5,
                max_value=100,
                step=1,
            ),
            StationConfigOption(
                key="popular_track_pool_size",
                label="Popular songs per artist",
                type="integer",
                description="How many popular songs Helix should consider from each selected artist.",
                default=15,
                min_value=5,
                max_value=50,
                step=1,
            ),
            StationConfigOption(
                key="seed_influence",
                label="Seed influence",
                type="number",
                description="Higher values make the seed artist and closest related artists more likely.",
                default=0.75,
                min_value=0,
                max_value=1,
                step=0.05,
            ),
            StationConfigOption(
                key="artist_cooldown",
                label="No repeated artist within",
                type="integer",
                description="Do not play an artist again until this many other tracks have passed. Set to 0 to disable.",
                default=5,
                min_value=0,
                max_value=50,
                step=1,
            ),
            StationConfigOption(
                key="artist_blacklist",
                label="Artist blacklist",
                type="textarea",
                description="Comma- or line-separated artist names this station should avoid.",
                default="",
            ),
        ]

    async def _yt_artist(self, artist: str) -> dict[str, Any]:
        artist = _clean(artist)
        if not artist:
            return {}
        key = ("artist", _norm_artist(artist))
        cached = self._cache_get(key)
        if cached is not None:
            return dict(cached)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(find_artist_by_name, artist, artist_limit=8),
                timeout=float(os.getenv("HELIX_YTMUSIC_LOOKUP_TIMEOUT_S", "8")),
            ) or {}
            if result:
                self._cache_put(key, dict(result))
            return result
        except Exception as exc:
            LOG.warning("Similar Artist YouTube Music lookup failed artist=%r err=%s", artist, exc)
            return {}

    async def _related_artists(self, browse_id: str, limit: int = 100) -> list[dict[str, Any]]:
        browse_id = _clean(browse_id)
        limit = max(1, int(limit))
        if not browse_id:
            return []
        key = ("related", browse_id, limit)
        cached = self._cache_get(key)
        if cached is not None:
            return [dict(row) for row in cached]
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(get_artist_related_artists, browse_id, limit=limit),
                timeout=float(os.getenv("HELIX_YTMUSIC_RELATED_TIMEOUT_S", "10")),
            ) or []
            if result:
                self._cache_put(key, [dict(row) for row in result if isinstance(row, dict)])
            return result
        except Exception as exc:
            LOG.warning("YouTube Music related artists failed browse_id=%s err=%s", browse_id, exc)
            return []

    async def _top_recordings(self, browse_id: str, limit: int) -> list[dict[str, Any]]:
        browse_id = _clean(browse_id)
        limit = max(1, int(limit))
        if not browse_id:
            return []
        key = ("songs", browse_id, limit)
        cached = self._cache_get(key)
        if cached is not None:
            return [dict(row) for row in cached]
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(get_artist_popular_songs, browse_id, limit=limit),
                timeout=float(os.getenv("HELIX_YTMUSIC_ARTIST_SONGS_TIMEOUT_S", "12")),
            ) or []
            if result:
                self._cache_put(key, [dict(row) for row in result if isinstance(row, dict)])
            return result
        except Exception as exc:
            LOG.warning("YouTube Music artist songs failed browse_id=%s err=%s", browse_id, exc)
            return []

    async def next_tracks(self, context: StationContext, count: int) -> list[StationResult]:
        cfg = context.config or {}
        self.validate_config(cfg)

        seed_artist = _clean(str(cfg.get("seed_artist") or ""))
        # Similar Artist Radio now exposes its breadth directly instead of using
        # the generic Safe/Balanced/Deep discovery preset. Preserve the old
        # presets for stations that have not yet been re-saved with the new
        # explicit controls.
        depth = str(cfg.get("discovery_depth") or "").strip().lower()
        legacy_depth = {
            "safe": (0.15, 12, 8),
            "balanced": (0.35, 35, 15),
            "deep": (0.75, 100, 50),
        }.get(depth)

        discovery = float(0.35 if cfg.get("discovery") is None else cfg.get("discovery"))
        if legacy_depth and cfg.get("related_artist_limit") is None:
            discovery = legacy_depth[0]
            related_limit = legacy_depth[1]
        else:
            related_limit = max(5, min(100, _infer_int(cfg.get("related_artist_limit"), 35)))

        if legacy_depth and cfg.get("popular_track_pool_size") is None:
            popular_track_pool_size = legacy_depth[2]
        else:
            popular_track_pool_size = max(5, min(50, _infer_int(cfg.get("popular_track_pool_size"), 15)))
        seed_influence = float(0.75 if cfg.get("seed_influence") is None else cfg.get("seed_influence"))
        artist_cooldown = max(0, min(50, _infer_int(cfg.get("artist_cooldown"), 5)))
        blacklist = {_norm_artist(a) for a in (cfg.get("artist_blacklist_items") or []) if a}

        seed_row = await self._yt_artist(seed_artist)
        seed_yt_id = _clean(str(seed_row.get("browse_id") or seed_row.get("artist_id") or ""))
        if not seed_yt_id:
            raise ValueError(f"Station seed artist not found on YouTube Music for {seed_artist!r}")

        related_artists = await self._related_artists(seed_yt_id, limit=related_limit)
        candidate_artists: list[dict[str, Any]] = [
            {
                "similar_artist_name": seed_artist,
                "yt_artist_id": seed_yt_id,
                "rank": 0,
            }
        ]
        for row in related_artists:
            if not isinstance(row, dict):
                continue
            name = _clean(str(row.get("name") or row.get("artist") or row.get("title") or ""))
            if not name:
                continue
            candidate_artists.append(
                {
                    "similar_artist_name": name,
                    "yt_artist_id": _clean(str(row.get("browse_id") or row.get("artist_id") or "")),
                    "rank": _infer_int(row.get("rank"), len(candidate_artists)),
                }
            )

        recent_pairs = {_norm_pair(t.title, t.artist) for t in context.recent_tracks}
        selected_pairs = {r.key() for r in context.already_selected}
        selected: list[StationResult] = []
        selected_artists: list[str] = [r.artist for r in context.already_selected]
        recordings_cache: dict[str, list[dict[str, Any]]] = {}
        artist_id_by_name = {
            _norm_artist(str(row.get("similar_artist_name") or "")): _clean(str(row.get("yt_artist_id") or ""))
            for row in candidate_artists
            if _clean(str(row.get("similar_artist_name") or ""))
        }
        attempts = max(count * 8, 12)

        for _ in range(attempts):
            if len(selected) >= max(1, int(count)):
                break
            blocked_artists = _blocked_artists_for_cooldown(context, selected, artist_cooldown)
            artist = _pick_station_artist(
                candidate_artists,
                seed_artist=seed_artist,
                seed_influence=seed_influence,
                discovery=discovery,
                recent_artists=context.recent_artists,
                blacklist=blacklist,
                selected_artists=selected_artists,
                blocked_artists=blocked_artists,
            )
            if not artist:
                break
            artist_key = _norm_artist(artist)
            yt_artist_id = artist_id_by_name.get(artist_key, "")
            if not yt_artist_id:
                artist_row = await self._yt_artist(artist)
                yt_artist_id = _clean(str(artist_row.get("browse_id") or artist_row.get("artist_id") or ""))
                if yt_artist_id:
                    artist_id_by_name[artist_key] = yt_artist_id
            if not yt_artist_id:
                continue

            if yt_artist_id not in recordings_cache:
                recordings_cache[yt_artist_id] = await self._top_recordings(yt_artist_id, popular_track_pool_size)
            recordings = list(recordings_cache.get(yt_artist_id) or [])
            if not recordings:
                continue
            random.shuffle(recordings)
            for recording in recordings:
                title = _clean_recording_name(recording)
                track_artist = _clean_artist_name(recording) or _clean(artist)
                if not title or not track_artist:
                    continue
                if _norm_artist(track_artist) in blocked_artists:
                    continue
                pair = _norm_pair(title, track_artist)
                if pair in recent_pairs or pair in selected_pairs:
                    continue
                result = StationResult(
                    title=title,
                    artist=track_artist,
                    album=_clean(str(recording.get("album") or "")),
                    duration_ms=_infer_int(
                        recording.get("duration_ms")
                        or (int(recording.get("duration_seconds") or 0) * 1000)
                        or recording.get("durationMs")
                        or 0,
                        0,
                    ),
                    reason=f"YouTube Music related artist: {artist}",
                    provider_metadata={
                        "yt_artist_id": yt_artist_id,
                        "video_id": str(recording.get("video_id") or recording.get("videoId") or ""),
                        "thumbnail_url": str(recording.get("thumbnail_url") or ""),
                        "discovery_source": "ytmusic",
                        "provider": self.station_type,
                    },
                )
                selected.append(result)
                selected_pairs.add(result.key())
                selected_artists.append(track_artist)
                break

        return selected[: max(1, int(count))]
