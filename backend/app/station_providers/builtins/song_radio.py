from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any

from ...integrations.ytmusic import find_song, get_song_radio
from ..base import StationProvider
from ..models import StationConfigOption, StationContext, StationResult

LOG = logging.getLogger("helix.station_providers.song_radio")


def _clean(value: str) -> str:
    return " ".join((value or "").strip().split())


def _norm(value: str) -> str:
    value = _clean(value).casefold()
    value = value.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", value).strip()


def _norm_artist(value: str) -> str:
    value = _norm(value)
    value = re.sub(r",\s*the$", "", value).strip()
    value = re.sub(r"^the\s+", "", value).strip()
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _pair(title: str, artist: str) -> str:
    return f"{_norm(title)}|{_norm_artist(artist)}"


def _blocked_artists(context: StationContext, selected: list[StationResult], cooldown: int) -> set[str]:
    cooldown = max(0, int(cooldown or 0))
    if cooldown <= 0:
        return set()
    sequence: list[str] = []
    sequence.extend(item.artist for item in reversed(selected))
    sequence.extend(item.artist for item in reversed(context.already_selected or []))
    sequence.extend(item.artist for item in reversed(context.queued_tracks or []))
    sequence.extend(item.artist for item in context.recent_tracks or [])
    return {_norm_artist(a) for a in sequence[:cooldown] if _norm_artist(a)}


class SongRadioProvider(StationProvider):
    station_type = "song_radio"
    display_name = "Song Radio"
    description = "Builds a station from YouTube Music recommendations around one seed song."
    version = "1.0.0"
    builtin = True

    def config_options(self) -> list[StationConfigOption]:
        # The frontend renders seed_title/seed_artist/seed_video_id as one song picker.
        return [
            StationConfigOption(
                key="discovery_depth",
                label="Discovery depth",
                type="select",
                description="Controls how far down the seed song's YouTube Music radio pool Helix explores.",
                default="balanced",
                choices=[
                    {"value": "safe", "label": "Safe - closest recommendations"},
                    {"value": "balanced", "label": "Balanced"},
                    {"value": "deep", "label": "Deep - broader recommendations"},
                ],
            ),
            StationConfigOption(
                key="seed_influence",
                label="Seed artist influence",
                type="number",
                description="Higher values make songs by the seed artist more likely when they appear in the radio pool.",
                default=0.35,
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

    def validate_config(self, config: dict[str, Any]) -> None:
        super().validate_config(config)
        if not _clean(str(config.get("seed_title") or "")):
            raise ValueError("Seed song is required")
        if not _clean(str(config.get("seed_artist") or "")):
            raise ValueError("Seed song artist is required")

    async def _resolve_seed_video_id(self, config: dict[str, Any]) -> str:
        video_id = _clean(str(config.get("seed_video_id") or config.get("yt_video_id") or ""))
        if video_id:
            return video_id
        title = _clean(str(config.get("seed_title") or ""))
        artist = _clean(str(config.get("seed_artist") or ""))
        try:
            match = await asyncio.wait_for(
                asyncio.to_thread(find_song, title=title, artist=artist),
                timeout=float(os.getenv("HELIX_YTMUSIC_LOOKUP_TIMEOUT_S", "8")),
            )
        except Exception as exc:
            LOG.warning("Song Radio seed resolution failed title=%r artist=%r err=%s", title, artist, exc)
            return ""
        return _clean(str(getattr(match, "video_id", "") or "")) if getattr(match, "found", False) else ""

    async def _radio(self, video_id: str, limit: int) -> list[dict[str, Any]]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(get_song_radio, video_id, limit=limit),
                timeout=float(os.getenv("HELIX_YTMUSIC_RADIO_TIMEOUT_S", "12")),
            ) or []
        except Exception as exc:
            LOG.warning("Song Radio YTM radio request failed video_id=%s err=%s", video_id, exc)
            return []

    async def next_tracks(self, context: StationContext, count: int) -> list[StationResult]:
        cfg = context.config or {}
        self.validate_config(cfg)

        seed_title = _clean(str(cfg.get("seed_title") or ""))
        seed_artist = _clean(str(cfg.get("seed_artist") or ""))
        seed_video_id = await self._resolve_seed_video_id(cfg)
        if not seed_video_id:
            raise ValueError(f"Seed song could not be resolved on YouTube Music: {seed_artist} - {seed_title}")

        depth = _clean(str(cfg.get("discovery_depth") or "balanced")).lower()
        pool_limit, rank_power = {
            "safe": (20, 2.0),
            "balanced": (50, 1.15),
            "deep": (100, 0.55),
        }.get(depth, (50, 1.15))
        seed_influence = max(0.0, min(1.0, float(0.35 if cfg.get("seed_influence") is None else cfg.get("seed_influence"))))
        artist_cooldown = max(0, min(50, int(5 if cfg.get("artist_cooldown") is None else cfg.get("artist_cooldown"))))
        blacklist_raw = str(cfg.get("artist_blacklist") or "")
        blacklist = {
            _norm_artist(part)
            for part in re.split(r"[,\n]+", blacklist_raw)
            if _norm_artist(part)
        }

        rows = await self._radio(seed_video_id, pool_limit)
        if not rows:
            return []

        unavailable_pairs = {_pair(t.title, t.artist) for t in context.recent_tracks or []}
        unavailable_pairs.update(_pair(t.title, t.artist) for t in context.queued_tracks or [])
        unavailable_pairs.update(result.key() for result in context.already_selected or [])
        selected: list[StationResult] = []
        selected_keys: set[str] = set()
        seed_artist_norm = _norm_artist(seed_artist)

        candidates: list[tuple[dict[str, Any], int]] = []
        for rank, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            title = _clean(str(row.get("title") or ""))
            artist = _clean(str(row.get("artist") or ""))
            video_id = _clean(str(row.get("video_id") or row.get("videoId") or ""))
            if not title or not artist or not video_id:
                continue
            # The seed frequently appears as the first watch-list row. Radio should
            # recommend around it rather than immediately replaying it.
            if video_id == seed_video_id or _pair(title, artist) == _pair(seed_title, seed_artist):
                continue
            if _norm_artist(artist) in blacklist:
                continue
            candidates.append((row, rank))

        attempts = max(12, int(count) * 10)
        for _ in range(attempts):
            if len(selected) >= max(1, int(count)) or not candidates:
                break
            blocked = _blocked_artists(context, selected, artist_cooldown)
            weighted: list[tuple[dict[str, Any], int, float]] = []
            for row, rank in candidates:
                title = _clean(str(row.get("title") or ""))
                artist = _clean(str(row.get("artist") or ""))
                key = _pair(title, artist)
                if key in unavailable_pairs or key in selected_keys:
                    continue
                artist_norm = _norm_artist(artist)
                if artist_norm in blocked or artist_norm in blacklist:
                    continue
                weight = (1.0 / float(rank + 1)) ** rank_power
                if seed_artist_norm and artist_norm == seed_artist_norm:
                    weight *= 1.0 + (2.0 * seed_influence)
                weighted.append((row, rank, max(0.0001, weight)))
            if not weighted:
                break
            chosen_row, chosen_rank, _ = random.choices(
                weighted,
                weights=[item[2] for item in weighted],
                k=1,
            )[0]
            title = _clean(str(chosen_row.get("title") or ""))
            artist = _clean(str(chosen_row.get("artist") or ""))
            result = StationResult(
                title=title,
                artist=artist,
                album=_clean(str(chosen_row.get("album") or "")),
                duration_ms=int(chosen_row.get("duration_ms") or 0),
                reason=f"YouTube Music song radio from {seed_artist} - {seed_title}",
                provider_metadata={
                    "video_id": _clean(str(chosen_row.get("video_id") or "")),
                    "thumbnail_url": _clean(str(chosen_row.get("thumbnail_url") or "")),
                    "seed_video_id": seed_video_id,
                    "radio_rank": chosen_rank,
                    "discovery_source": "ytmusic",
                    "provider": self.station_type,
                },
            )
            selected.append(result)
            selected_keys.add(_pair(title, artist))
            candidates = [(row, rank) for row, rank in candidates if _pair(str(row.get("title") or ""), str(row.get("artist") or "")) != _pair(title, artist)]

        return selected[: max(1, int(count))]
