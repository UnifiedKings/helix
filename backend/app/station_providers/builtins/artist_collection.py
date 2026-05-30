from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any

from ...integrations.listenbrainz import lb_top_recordings_for_artist
from ...integrations.musicbrainz import lookup_artist_mbid_by_name
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
    description = "Only plays tracks from the selected seed artists, with simple rotation and repeat controls."
    version = "1.0.0"
    builtin = True

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
                key="max_consecutive_same_artist",
                label="Max consecutive same artist",
                type="integer",
                description="When multiple seed artists are configured, avoid playing more than this many songs by the same artist in a row.",
                default=1,
                min_value=1,
                max_value=10,
                step=1,
            ),
            StationConfigOption(
                key="popular_track_pool_size",
                label="Popular track pool size",
                type="integer",
                description="How many ListenBrainz top recordings to sample for each seed artist. Smaller values favor hits; larger values allow more variety, especially for single-artist stations.",
                default=25,
                min_value=1,
                max_value=1000,
                step=1,
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

    async def _artist_mbid(self, artist: str) -> str:
        if not _clean(artist):
            return ""
        try:
            return await asyncio.wait_for(
                lookup_artist_mbid_by_name(artist),
                timeout=float(os.getenv("HELIX_MB_LOOKUP_TIMEOUT_S", "8")),
            ) or ""
        except Exception as exc:
            LOG.warning("Artist Collection MBID lookup failed artist=%r err=%s", artist, exc)
            return ""

    async def _top_recordings(self, artist: str, limit: int) -> list[dict[str, Any]]:
        mbid = await self._artist_mbid(artist)
        if not mbid:
            return []
        try:
            recordings = await asyncio.wait_for(
                lb_top_recordings_for_artist(mbid, max(1, int(limit))),
                timeout=float(os.getenv("HELIX_LB_TOP_TRACK_TIMEOUT_S", "10")),
            ) or []
        except Exception as exc:
            LOG.warning("Artist Collection top recordings failed artist=%r mbid=%s err=%s", artist, mbid, exc)
            return []
        return [dict(item, _helix_seed_artist=artist, _helix_artist_mbid=mbid) for item in recordings if isinstance(item, dict)]

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

    def _blocked_by_consecutive_limit(self, artist: str, context: StationContext, selected: list[StationResult], max_consecutive: int, artist_count: int) -> bool:
        if artist_count <= 1:
            return False
        max_consecutive = max(1, int(max_consecutive or 1))
        artist_key = _norm_artist(artist)
        recent_sequence: list[str] = []
        for result in reversed(selected):
            recent_sequence.append(_norm_artist(result.artist))
        for result in reversed(context.already_selected or []):
            recent_sequence.append(_norm_artist(result.artist))
        for item in reversed(context.queued_tracks or []):
            recent_sequence.append(_norm_artist(item.artist))
        streak = 0
        for key in recent_sequence:
            if not key:
                continue
            if key == artist_key:
                streak += 1
                if streak >= max_consecutive:
                    return True
            else:
                break
        return False

    async def next_tracks(self, context: StationContext, count: int) -> list[StationResult]:
        cfg = context.config or {}
        self.validate_config(cfg)

        seed_artists = _parse_artists(cfg.get("seed_artists"))
        rotation_mode = str(cfg.get("rotation_mode") or "balanced").strip().lower()
        max_consecutive = max(1, min(10, _safe_int(cfg.get("max_consecutive_same_artist"), 1)))
        pool_size = max(1, min(1000, _safe_int(cfg.get("popular_track_pool_size"), 25)))
        recent_window = max(0, min(500, _safe_int(cfg.get("recent_track_window"), 75)))
        wanted = max(1, int(count or 1))

        recording_groups = await asyncio.gather(*(self._top_recordings(artist, pool_size) for artist in seed_artists))
        by_artist: dict[str, list[dict[str, Any]]] = {}
        display_by_key: dict[str, str] = {}
        for artist, recordings in zip(seed_artists, recording_groups):
            key = _norm_artist(artist)
            if not key:
                continue
            display_by_key[key] = artist
            cleaned = [r for r in recordings if _recording_title(r)]
            random.shuffle(cleaned)
            by_artist[key] = cleaned

        if not any(by_artist.values()):
            return []

        recent_pairs = {
            _norm_pair(item.title, item.artist)
            for item in list(context.recent_tracks or [])[:recent_window]
        }
        queued_pairs = {_norm_pair(item.title, item.artist) for item in context.queued_tracks or []}
        selected_pairs = {item.key() for item in context.already_selected or []}
        used_pairs = recent_pairs | queued_pairs | selected_pairs

        selected: list[StationResult] = []
        counts = self._artist_counts(context, seed_artists)
        artist_keys = [key for key in (_norm_artist(a) for a in seed_artists) if key in by_artist and by_artist[key]]

        attempts = max(wanted * max(len(artist_keys), 1) * 5, 20)
        for _ in range(attempts):
            if len(selected) >= wanted:
                break
            available = [key for key in artist_keys if by_artist.get(key)]
            if not available:
                break
            if rotation_mode == "balanced":
                min_count = min(counts.get(key, 0) for key in available)
                artist_key = random.choice([key for key in available if counts.get(key, 0) == min_count])
            else:
                artist_key = random.choice(available)

            if self._blocked_by_consecutive_limit(display_by_key.get(artist_key, artist_key), context, selected, max_consecutive, len(artist_keys)):
                alternatives = [key for key in available if key != artist_key]
                if alternatives:
                    artist_key = random.choice(alternatives)

            recordings = by_artist.get(artist_key) or []
            while recordings:
                recording = recordings.pop(0)
                seed_artist = _clean(str(recording.get("_helix_seed_artist") or display_by_key.get(artist_key, "")))
                title = _recording_title(recording)
                artist = _recording_artist(recording, seed_artist)
                if _norm_artist(artist) != artist_key:
                    # ListenBrainz occasionally returns credited collaborations or noisy rows.
                    # This provider is intentionally strict: only seed artists are allowed.
                    artist = seed_artist
                pair = _norm_pair(title, artist)
                if not title or not artist or pair in used_pairs:
                    continue
                duration_ms = _safe_int(recording.get("duration_ms") or recording.get("durationMs") or 0, 0)
                result = StationResult(
                    title=title,
                    artist=artist,
                    duration_ms=duration_ms,
                    reason=f"Artist Collection seed artist: {seed_artist}",
                    provider_metadata={
                        "provider": self.station_type,
                        "artist_mbid": str(recording.get("_helix_artist_mbid") or ""),
                        "recording_mbid": str(recording.get("recording_mbid") or recording.get("recording_id") or ""),
                    },
                )
                selected.append(result)
                used_pairs.add(pair)
                counts[artist_key] = counts.get(artist_key, 0) + 1
                break

        return selected[:wanted]
