from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from typing import Any

from ...integrations.ytmusic import find_artist_by_name, get_artist_popular_songs
from ..base import StationProvider
from ..models import StationConfigOption, StationContext, StationResult

LOG = logging.getLogger("helix.station_providers.artist_collection")


def _clean(value: str) -> str:
    return " ".join((value or "").strip().split())


def _norm(value: str) -> str:
    value = _clean(value).lower()
    value = value.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _norm_artist(value: str) -> str:
    artist = _norm(value)
    if not artist:
        return ""
    artist = re.sub(r",\s*the$", "", artist).strip()
    artist = re.sub(r"^the\s+", "", artist).strip()
    artist = re.sub(r"[^a-z0-9\s]+", " ", artist)
    artist = re.sub(r"\s+", " ", artist).strip()
    return artist


def _norm_pair(title: str, artist: str) -> str:
    return f"{_norm(title)}|{_norm_artist(artist)}"




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
    # recent_tracks is provided newest-first by the station engine.
    sequence.extend(item.artist for item in context.recent_tracks or [])

    return {_norm_artist(artist) for artist in sequence[:cooldown] if _norm_artist(artist)}

def _parse_artists(raw: Any) -> list[str]:
    if isinstance(raw, list):
        parts = [str(item or "") for item in raw]
    else:
        parts = re.split(r"[,\n]", str(raw or ""))
    artists: list[str] = []
    seen: set[str] = set()
    for part in parts:
        artist = _clean(part)
        key = _norm_artist(artist)
        if not artist or not key or key in seen:
            continue
        artists.append(artist)
        seen.add(key)
    return artists


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _recording_title(item: dict[str, Any]) -> str:
    return _clean(str(item.get("recording_name") or item.get("track_name") or item.get("title") or item.get("name") or ""))


def _recording_artist(item: dict[str, Any], fallback: str) -> str:
    return _clean(str(item.get("artist_name") or item.get("artist") or item.get("artist_credit_name") or fallback or ""))


class ArtistCollectionProvider(StationProvider):
    station_type = "artist_collection"
    display_name = "Artist Collection"
    description = "Uses YouTube Music artist catalogs and only plays tracks from the selected seed artists, with simple rotation and repeat controls."
    version = "1.2.1"
    builtin = True

    def __init__(self) -> None:
        # The registry keeps one provider instance alive. Artist Collection can
        # otherwise re-resolve every seed artist and re-fetch every catalog on
        # every queue refill, even when the same station was filled seconds ago.
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
                key="seed_artists",
                label="Seed artists",
                type="textarea",
                description="Comma- or line-separated list of artists this station is allowed to play.",
                required=True,
                default="",
            ),
            StationConfigOption(
                key="rotation_mode",
                label="Artist rotation",
                type="select",
                description="Balanced rotation avoids letting one seed artist dominate. Random rotation picks freely from the seed list.",
                default="balanced",
                choices=[
                    {"value": "balanced", "label": "Balanced"},
                    {"value": "random", "label": "Random"},
                ],
            ),
            StationConfigOption(
                key="artist_cooldown",
                label="No repeated artist within",
                type="integer",
                description="Do not play an artist again until this many other tracks have passed. Set to 0 to disable. Ignored when this collection contains only one seed artist.",
                default=5,
                min_value=0,
                max_value=50,
                step=1,
            ),
            StationConfigOption(
                key="discovery_depth",
                label="Discovery depth",
                type="select",
                description="Controls how far into each seed artist's YouTube Music catalog this station explores.",
                default="balanced",
                choices=[
                    {"value": "safe", "label": "Safe - favor familiar songs"},
                    {"value": "balanced", "label": "Balanced"},
                    {"value": "deep", "label": "Deep - explore more of the catalog"},
                ],
            ),
            StationConfigOption(
                key="recent_track_window",
                label="Recent track window",
                type="integer",
                description="Avoid repeating tracks that appeared within this many recent history/queue items.",
                default=75,
                min_value=0,
                max_value=500,
                step=1,
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> None:
        super().validate_config(config)
        if not _parse_artists(config.get("seed_artists")):
            raise ValueError("Seed artists is required")
        rotation_mode = str(config.get("rotation_mode") or "balanced").strip().lower()
        if rotation_mode not in {"balanced", "random"}:
            raise ValueError("Artist rotation must be balanced or random")

    async def _yt_artist(self, artist: str) -> dict[str, Any]:
        artist = _clean(artist)
        if not artist:
            return {}
        cache_key = ("artist", _norm_artist(artist))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return dict(cached)
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(find_artist_by_name, artist, artist_limit=8),
                timeout=float(os.getenv("HELIX_YTMUSIC_LOOKUP_TIMEOUT_S", "8")),
            ) or {}
            return dict(self._cache_put(cache_key, dict(result)))
        except Exception as exc:
            LOG.warning("Artist Collection YouTube Music artist lookup failed artist=%r err=%s", artist, exc)
            return {}

    async def _top_recordings(self, artist: str, limit: int) -> list[dict[str, Any]]:
        yt_artist = await self._yt_artist(artist)
        browse_id = _clean(str(yt_artist.get("browse_id") or yt_artist.get("artist_id") or ""))
        if not browse_id:
            return []
        wanted = max(1, int(limit))
        cache_key = ("songs", browse_id, wanted)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return [dict(item) for item in cached]
        try:
            recordings = await asyncio.wait_for(
                asyncio.to_thread(get_artist_popular_songs, browse_id, limit=wanted),
                timeout=float(os.getenv("HELIX_YTMUSIC_ARTIST_SONGS_TIMEOUT_S", "12")),
            ) or []
        except Exception as exc:
            LOG.warning("Artist Collection YouTube Music songs failed artist=%r browse_id=%s err=%s", artist, browse_id, exc)
            return []
        cleaned = [dict(item, _helix_seed_artist=artist, _helix_yt_artist_id=browse_id) for item in recordings if isinstance(item, dict)]
        self._cache_put(cache_key, [dict(item) for item in cleaned])
        return cleaned

    def _artist_counts(self, context: StationContext, seed_artists: list[str]) -> dict[str, int]:
        seed_keys = {_norm_artist(a) for a in seed_artists}
        counts = {key: 0 for key in seed_keys if key}
        for item in list(context.queued_tracks or []) + list(context.recent_tracks or []):
            key = _norm_artist(item.artist)
            if key in counts:
                counts[key] += 1
        for item in context.already_selected or []:
            key = _norm_artist(item.artist)
            if key in counts:
                counts[key] += 1
        return counts

    async def next_tracks(self, context: StationContext, count: int) -> list[StationResult]:
        cfg = context.config or {}
        self.validate_config(cfg)

        seed_artists = _parse_artists(cfg.get("seed_artists"))
        rotation_mode = str(cfg.get("rotation_mode") or "balanced").strip().lower()
        artist_cooldown = max(0, min(50, _safe_int(cfg.get("artist_cooldown"), 5)))
        depth = str(cfg.get("discovery_depth") or "").strip().lower()
        if depth in {"safe", "balanced", "deep"}:
            pool_size = {"safe": 10, "balanced": 25, "deep": 100}[depth]
        else:
            # Backward compatibility for stations saved before discovery_depth existed.
            pool_size = max(1, min(1000, _safe_int(cfg.get("popular_track_pool_size"), 25)))
        recent_window = max(0, min(500, _safe_int(cfg.get("recent_track_window"), 75)))
        wanted = max(1, int(count or 1))

        recent_pairs = {
            _norm_pair(item.title, item.artist)
            for item in list(context.recent_tracks or [])[:recent_window]
        }
        queued_pairs = {_norm_pair(item.title, item.artist) for item in context.queued_tracks or []}
        selected_pairs = {item.key() for item in context.already_selected or []}
        used_pairs = recent_pairs | queued_pairs | selected_pairs

        selected: list[StationResult] = []
        counts = self._artist_counts(context, seed_artists)
        by_artist: dict[str, list[dict[str, Any]]] = {}
        display_by_key = {_norm_artist(artist): artist for artist in seed_artists if _norm_artist(artist)}

        # Loading every configured artist catalog before picking even one track is
        # wasteful for large collections. Start with only the artists most likely
        # to be selected, then expand to the rest only if duplicates/unavailable
        # tracks prevent us from satisfying the requested batch.
        candidate_artists = list(seed_artists)
        if rotation_mode == "balanced":
            random.shuffle(candidate_artists)  # random tie-break for equal counts
            candidate_artists.sort(key=lambda artist: counts.get(_norm_artist(artist), 0))
        else:
            random.shuffle(candidate_artists)

        initial_artist_count = min(
            len(candidate_artists),
            max(4, wanted * 2, min(len(candidate_artists), artist_cooldown + 1)),
        )
        initial_artists = candidate_artists[:initial_artist_count]
        remaining_artists = candidate_artists[initial_artist_count:]

        async def load_artists(artists: list[str]) -> None:
            if not artists:
                return
            recording_groups = await asyncio.gather(*(self._top_recordings(artist, pool_size) for artist in artists))
            for artist, recordings in zip(artists, recording_groups):
                key = _norm_artist(artist)
                if not key:
                    continue
                cleaned = [r for r in recordings if _recording_title(r)]
                random.shuffle(cleaned)
                by_artist[key] = cleaned

        def pick_from_loaded() -> None:
            artist_keys = [key for key in (_norm_artist(a) for a in candidate_artists) if key in by_artist and by_artist[key]]
            attempts = max((wanted - len(selected)) * max(len(artist_keys), 1) * 5, 20)
            for _ in range(attempts):
                if len(selected) >= wanted:
                    break
                available = [key for key in artist_keys if by_artist.get(key)]
                if len(artist_keys) > 1 and artist_cooldown > 0:
                    blocked_artists = _blocked_artists_for_cooldown(context, selected, artist_cooldown)
                    available = [key for key in available if key not in blocked_artists]
                if not available:
                    break
                if rotation_mode == "balanced":
                    min_count = min(counts.get(key, 0) for key in available)
                    artist_key = random.choice([key for key in available if counts.get(key, 0) == min_count])
                else:
                    artist_key = random.choice(available)

                recordings = by_artist.get(artist_key) or []
                while recordings:
                    recording = recordings.pop(0)
                    seed_artist = _clean(str(recording.get("_helix_seed_artist") or display_by_key.get(artist_key, "")))
                    title = _recording_title(recording)
                    artist = _recording_artist(recording, seed_artist)
                    if _norm_artist(artist) != artist_key:
                        # YouTube Music can return collaboration credits on artist pages.
                        # This provider is intentionally strict: only seed artists are allowed.
                        artist = seed_artist
                    pair = _norm_pair(title, artist)
                    if not title or not artist or pair in used_pairs:
                        continue
                    duration_ms = _safe_int(recording.get("duration_ms") or recording.get("durationMs") or 0, 0)
                    result = StationResult(
                        title=title,
                        artist=artist,
                        album=_clean(str(recording.get("album") or "")),
                        duration_ms=duration_ms,
                        reason=f"Artist Collection seed artist: {seed_artist}",
                        provider_metadata={
                            "provider": self.station_type,
                            "yt_artist_id": str(recording.get("_helix_yt_artist_id") or ""),
                            "video_id": str(recording.get("video_id") or recording.get("videoId") or ""),
                            "thumbnail_url": str(recording.get("thumbnail_url") or ""),
                            "discovery_source": "ytmusic",
                        },
                    )
                    selected.append(result)
                    used_pairs.add(pair)
                    counts[artist_key] = counts.get(artist_key, 0) + 1
                    break

        await load_artists(initial_artists)
        pick_from_loaded()
        if len(selected) < wanted and remaining_artists:
            await load_artists(remaining_artists)
            pick_from_loaded()

        return selected[:wanted]
