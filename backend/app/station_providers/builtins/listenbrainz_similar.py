from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any

from ...integrations.listenbrainz import lb_similar_artists_for_artist, lb_top_recordings_for_artist
from ...integrations.musicbrainz import lookup_artist_mbid_by_name
from ..base import StationProvider
from ..models import StationConfigOption, StationContext, StationResult

LOG = logging.getLogger("helix.station_providers.listenbrainz")


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
) -> str:
    seed_n = _norm_artist(seed_artist or "")
    recent_set = {_norm_artist(a) for a in (recent_artists or [])[:50] if a}
    selected_set = {_norm_artist(a) for a in (selected_artists or []) if a}

    parsed: list[tuple[str, int]] = []
    for it in candidate_artists or []:
        name = _clean(str(it.get("similar_artist_name") or it.get("artist_name") or it.get("artist") or ""))
        if not name:
            continue
        na = _norm_artist(name)
        if not na or na in blacklist:
            continue
        rank = _infer_int(it.get("rank") or it.get("similar_artist_rank") or 0, 0)
        parsed.append((name, max(0, rank)))

    if not parsed:
        return seed_artist or ""

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


class ListenBrainzSimilarArtistProvider(StationProvider):
    station_type = "listenbrainz_similar_artist"
    display_name = "Similar Artist Radio"
    description = "Uses ListenBrainz similar artists and top recordings to recommend station tracks."
    version = "1.0.0"
    builtin = True

    def config_options(self) -> list[StationConfigOption]:
        return [
            StationConfigOption(
                key="seed_artist",
                label="Seed artist",
                type="string",
                description="The artist used as the anchor for ListenBrainz similar-artist recommendations.",
                required=True,
            ),
            StationConfigOption(
                key="discovery",
                label="Discovery",
                type="number",
                description="Higher values flatten the similar-artist weighting so the station wanders farther from the seed artist.",
                default=0.35,
                min_value=0,
                max_value=1,
                step=0.05,
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
                key="popular_track_pool_size",
                label="Popular track pool size",
                type="integer",
                description="How many of each selected artist's top ListenBrainz recordings can be sampled. Smaller numbers favor familiar songs; larger numbers allow deeper cuts.",
                default=10,
                min_value=1,
                max_value=1000,
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

    async def _artist_mbid(self, artist: str, existing_mbid: str = "") -> str:
        mbid = _clean(existing_mbid)
        if mbid:
            return mbid
        if not _clean(artist):
            return ""
        try:
            return await asyncio.wait_for(
                lookup_artist_mbid_by_name(artist),
                timeout=float(os.getenv("HELIX_MB_LOOKUP_TIMEOUT_S", "8")),
            )
        except Exception:
            return ""

    async def _top_recordings(self, artist_mbid: str, limit: int) -> list[dict[str, Any]]:
        if not artist_mbid:
            return []
        try:
            return await asyncio.wait_for(
                lb_top_recordings_for_artist(artist_mbid, max(1, int(limit))),
                timeout=float(os.getenv("HELIX_LB_TOP_TRACK_TIMEOUT_S", "10")),
            ) or []
        except Exception as exc:
            LOG.warning("ListenBrainz top recordings failed artist_mbid=%s err=%s", artist_mbid, exc)
            return []

    async def next_tracks(self, context: StationContext, count: int) -> list[StationResult]:
        cfg = context.config or {}
        self.validate_config(cfg)

        seed_artist = _clean(str(cfg.get("seed_artist") or ""))
        mb_artist_id = _clean(str(cfg.get("mb_artist_id") or ""))
        discovery = float(cfg.get("discovery", 0.35) or 0.35)
        seed_influence = float(cfg.get("seed_influence", 0.75) or 0.75)
        popular_track_pool_size = max(1, int(cfg.get("popular_track_pool_size", 10) or 10))
        blacklist = {_norm_artist(a) for a in (cfg.get("artist_blacklist_items") or []) if a}

        mb_artist_id = await self._artist_mbid(seed_artist, mb_artist_id)
        if not mb_artist_id:
            raise ValueError(f"Station seed artist MBID not found for {seed_artist!r}")

        try:
            similar_artists = await asyncio.wait_for(
                lb_similar_artists_for_artist(mb_artist_id, limit=100),
                timeout=float(os.getenv("HELIX_LB_SIMILAR_TIMEOUT_S", "10")),
            ) or []
        except Exception as exc:
            LOG.warning("ListenBrainz similar artists failed seed=%s err=%s", seed_artist, exc)
            similar_artists = []

        candidate_artists: list[dict[str, Any]] = []
        if seed_artist:
            candidate_artists.append({"similar_artist_name": seed_artist, "rank": 0})
        candidate_artists.extend(similar_artists)

        recent_pairs = {_norm_pair(t.title, t.artist) for t in context.recent_tracks}
        selected_pairs = {r.key() for r in context.already_selected}
        selected: list[StationResult] = []
        selected_artists: list[str] = [r.artist for r in context.already_selected]
        attempts = max(count * 8, 12)

        for _ in range(attempts):
            if len(selected) >= max(1, int(count)):
                break
            artist = _pick_station_artist(
                candidate_artists,
                seed_artist=seed_artist,
                seed_influence=seed_influence,
                discovery=discovery,
                recent_artists=context.recent_artists,
                blacklist=blacklist,
                selected_artists=selected_artists,
            )
            artist_mbid = await self._artist_mbid(artist)
            recordings = await self._top_recordings(artist_mbid, popular_track_pool_size)
            if not recordings:
                continue
            random.shuffle(recordings)
            for recording in recordings:
                title = _clean_recording_name(recording)
                track_artist = _clean_artist_name(recording) or _clean(artist)
                if not title or not track_artist:
                    continue
                pair = _norm_pair(title, track_artist)
                if pair in recent_pairs or pair in selected_pairs:
                    continue
                result = StationResult(
                    title=title,
                    artist=track_artist,
                    duration_ms=_infer_int(recording.get("duration_ms") or recording.get("durationMs") or 0, 0),
                    reason=f"ListenBrainz similar artist: {artist}",
                    provider_metadata={
                        "artist_mbid": artist_mbid,
                        "recording_mbid": str(recording.get("recording_mbid") or recording.get("recording_id") or ""),
                        "provider": self.station_type,
                    },
                )
                selected.append(result)
                selected_pairs.add(result.key())
                selected_artists.append(track_artist)
                break

        return selected[: max(1, int(count))]
