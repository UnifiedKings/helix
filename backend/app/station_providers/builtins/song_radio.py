from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time
from typing import Any

from ...integrations.ytmusic import find_song, get_song_radio
from ..base import StationProvider
from ..models import StationConfigOption, StationContext, StationResult

LOG = logging.getLogger("helix.station_providers.song_radio")


_POOL_MAX_STATIONS = 128
_POOL_MAX_MULTIPLIER = 12
_DISTANCE_DECAY = 0.68
_MIN_EXPANSION_SCORE = 0.08


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


def _rank_closeness(rank: int) -> float:
    """Stable 0..1 anchor score for a recommendation's position in a radio list."""
    return 1.0 / float(max(1, int(rank) + 1)) ** 0.35


def _combine_evidence(existing: float, contribution: float) -> float:
    """Combine independent recommendation paths with diminishing returns."""
    a = max(0.0, min(1.0, float(existing or 0.0)))
    b = max(0.0, min(1.0, float(contribution or 0.0)))
    return 1.0 - ((1.0 - a) * (1.0 - b))


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
    description = (
        "Starts with one song and builds a growing recommendation pool around it. "
        "Helix can use strong recommendations as additional discovery seeds up to your Discovery Reach, "
        "while scoring every song by how strongly it connects back to the original seed."
    )
    version = "1.2.0"
    builtin = True

    def __init__(self) -> None:
        # Providers are registry singletons, so this pool survives queue refills for the
        # life of the backend process. It is intentionally ephemeral: restarting Helix
        # simply rebuilds discovery from the station's original seed.
        self._discovery_pools: dict[str, dict[str, Any]] = {}
        self._pool_locks: dict[str, asyncio.Lock] = {}

    def config_options(self) -> list[StationConfigOption]:
        # The frontend renders seed_title/seed_artist/seed_video_id as one song picker.
        return [
            StationConfigOption(
                key="candidate_pool_size",
                label="Songs per discovery step",
                type="integer",
                description=(
                    "How many ranked YouTube Music recommendations Helix gathers each time it explores a seed. "
                    "This is not a lifetime song limit; the station's discovered pool grows as it runs."
                ),
                default=50,
                min_value=10,
                max_value=100,
                step=5,
            ),
            StationConfigOption(
                key="discovery_reach",
                label="Discovery reach",
                type="integer",
                description=(
                    "How far from the original song Helix may choose an additional song as a discovery seed. "
                    "0 only uses the original seed; 1 may branch from direct recommendations; higher values "
                    "allow progressively broader exploration. The original song always remains the anchor."
                ),
                default=1,
                min_value=0,
                max_value=3,
                step=1,
            ),
            StationConfigOption(
                key="top_recommendation_bias",
                label="Favor close recommendations",
                type="number",
                description=(
                    "How strongly Helix favors songs with stronger connections back to the original seed. "
                    "0 makes eligible songs nearly equal; higher values keep playback more tightly anchored."
                ),
                default=1.15,
                min_value=0,
                max_value=3,
                step=0.05,
            ),
            StationConfigOption(
                key="seed_influence",
                label="Seed artist influence",
                type="number",
                description="Higher values make songs by the seed artist more likely when they appear in the discovered pool.",
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

    def _pool_for(
        self,
        station_id: str,
        *,
        seed_video_id: str,
        pool_limit: int,
        discovery_reach: int,
    ) -> dict[str, Any]:
        fingerprint = (seed_video_id, int(pool_limit), int(discovery_reach))
        pool = self._discovery_pools.get(station_id)
        if not pool or pool.get("fingerprint") != fingerprint:
            pool = {
                "fingerprint": fingerprint,
                "candidates": {},
                "expanded_video_ids": {seed_video_id},
                "expansion_cursor": 0,
                "last_used": time.monotonic(),
            }
            self._discovery_pools[station_id] = pool
        pool["last_used"] = time.monotonic()
        self._trim_station_pools(exclude=station_id)
        return pool

    def _trim_station_pools(self, *, exclude: str) -> None:
        if len(self._discovery_pools) <= _POOL_MAX_STATIONS:
            return
        victims = sorted(
            (
                (station_id, float(pool.get("last_used") or 0.0))
                for station_id, pool in self._discovery_pools.items()
                if station_id != exclude
            ),
            key=lambda item: item[1],
        )
        for station_id, _ in victims[: max(0, len(self._discovery_pools) - _POOL_MAX_STATIONS)]:
            self._discovery_pools.pop(station_id, None)
            self._pool_locks.pop(station_id, None)

    @staticmethod
    def _merge_rows(
        pool: dict[str, Any],
        rows: list[dict[str, Any]],
        *,
        source_video_id: str,
        source_depth: int,
        source_score: float,
        seed_video_id: str,
        seed_title: str,
        seed_artist: str,
        blacklist: set[str],
    ) -> None:
        candidates: dict[str, dict[str, Any]] = pool["candidates"]
        for rank, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            title = _clean(str(row.get("title") or ""))
            artist = _clean(str(row.get("artist") or ""))
            video_id = _clean(str(row.get("video_id") or row.get("videoId") or ""))
            if not title or not artist or not video_id:
                continue
            key = _pair(title, artist)
            if video_id == seed_video_id or key == _pair(seed_title, seed_artist):
                continue
            if _norm_artist(artist) in blacklist:
                continue

            depth = source_depth + 1
            rank_score = _rank_closeness(rank)
            contribution = rank_score if source_depth == 0 else source_score * _DISTANCE_DECAY * rank_score
            existing = candidates.get(key)
            if existing is None:
                candidates[key] = {
                    "row": dict(row),
                    "video_id": video_id,
                    "score": max(0.0001, min(1.0, contribution)),
                    "best_depth": depth,
                    "support_count": 1,
                    "best_rank": rank,
                    "source_video_ids": {source_video_id},
                }
                continue

            # Repeated appearances through different recommendation paths are useful
            # evidence that a song belongs near the original seed.
            previous_depth = int(existing.get("best_depth") or depth)
            previous_rank = int(existing.get("best_rank") or rank)
            source_ids: set[str] = existing.setdefault("source_video_ids", set())
            if source_video_id not in source_ids:
                existing["score"] = _combine_evidence(float(existing.get("score") or 0.0), contribution)
                existing["support_count"] = int(existing.get("support_count") or 1) + 1
                source_ids.add(source_video_id)
            else:
                existing["score"] = max(float(existing.get("score") or 0.0), contribution)
            existing["best_depth"] = min(previous_depth, depth)
            existing["best_rank"] = min(previous_rank, rank)
            if depth < previous_depth or (depth == previous_depth and rank < previous_rank):
                existing["row"] = dict(row)
                existing["video_id"] = video_id

    @staticmethod
    def _choose_expansion_seed(pool: dict[str, Any], discovery_reach: int) -> dict[str, Any] | None:
        if discovery_reach <= 0:
            return None
        expanded: set[str] = pool["expanded_video_ids"]
        candidates: dict[str, dict[str, Any]] = pool["candidates"]

        by_depth: dict[int, list[dict[str, Any]]] = {}
        for candidate in candidates.values():
            depth = int(candidate.get("best_depth") or 999)
            video_id = _clean(str(candidate.get("video_id") or ""))
            score = float(candidate.get("score") or 0.0)
            if not video_id or video_id in expanded or depth < 1 or depth > discovery_reach:
                continue
            if score < _MIN_EXPANSION_SCORE:
                continue
            by_depth.setdefault(depth, []).append(candidate)

        if not by_depth:
            return None

        # Reach the allowed depth early, then rotate between depths so the graph
        # grows both outward and sideways instead of exhausting all depth-1 songs first.
        deepest_expanded = 0
        for candidate in candidates.values():
            if _clean(str(candidate.get("video_id") or "")) in expanded:
                deepest_expanded = max(deepest_expanded, int(candidate.get("best_depth") or 0))
        next_unreached_depth = min(discovery_reach, deepest_expanded + 1)
        if next_unreached_depth in by_depth and next_unreached_depth > deepest_expanded:
            target_depth = next_unreached_depth
        else:
            available_depths = sorted(by_depth)
            cursor = int(pool.get("expansion_cursor") or 0)
            target_depth = available_depths[cursor % len(available_depths)]
            pool["expansion_cursor"] = cursor + 1

        options = by_depth[target_depth]
        # Strong anchor score is the primary signal. Multi-path support breaks ties
        # in favor of songs independently recommended from several nearby branches.
        options.sort(
            key=lambda candidate: (
                float(candidate.get("score") or 0.0),
                int(candidate.get("support_count") or 0),
                -int(candidate.get("best_rank") or 999),
            ),
            reverse=True,
        )
        return options[0] if options else None

    @staticmethod
    def _prune_candidates(pool: dict[str, Any], pool_limit: int) -> None:
        candidates: dict[str, dict[str, Any]] = pool["candidates"]
        max_candidates = max(250, int(pool_limit) * _POOL_MAX_MULTIPLIER)
        if len(candidates) <= max_candidates:
            return
        keep = sorted(
            candidates.items(),
            key=lambda item: (
                float(item[1].get("score") or 0.0),
                int(item[1].get("support_count") or 0),
                -int(item[1].get("best_depth") or 999),
            ),
            reverse=True,
        )[:max_candidates]
        pool["candidates"] = dict(keep)

    async def next_tracks(self, context: StationContext, count: int) -> list[StationResult]:
        cfg = context.config or {}
        self.validate_config(cfg)

        seed_title = _clean(str(cfg.get("seed_title") or ""))
        seed_artist = _clean(str(cfg.get("seed_artist") or ""))
        seed_video_id = await self._resolve_seed_video_id(cfg)
        if not seed_video_id:
            raise ValueError(f"Seed song could not be resolved on YouTube Music: {seed_artist} - {seed_title}")

        # Existing stations may still have the old Safe/Balanced/Deep setting.
        legacy_depth = _clean(str(cfg.get("discovery_depth") or "")).lower()
        legacy_pool_limit, legacy_rank_power = {
            "safe": (20, 2.0),
            "balanced": (50, 1.15),
            "deep": (100, 0.55),
        }.get(legacy_depth, (50, 1.15))
        pool_limit = max(10, min(100, int(
            legacy_pool_limit if cfg.get("candidate_pool_size") is None else cfg.get("candidate_pool_size")
        )))
        rank_power = max(0.0, min(3.0, float(
            legacy_rank_power if cfg.get("top_recommendation_bias") is None else cfg.get("top_recommendation_bias")
        )))
        discovery_reach = max(0, min(3, int(1 if cfg.get("discovery_reach") is None else cfg.get("discovery_reach"))))
        seed_influence = max(0.0, min(1.0, float(0.35 if cfg.get("seed_influence") is None else cfg.get("seed_influence"))))
        artist_cooldown = max(0, min(50, int(5 if cfg.get("artist_cooldown") is None else cfg.get("artist_cooldown"))))
        blacklist_raw = str(cfg.get("artist_blacklist") or "")
        blacklist = {
            _norm_artist(part)
            for part in re.split(r"[,\n]+", blacklist_raw)
            if _norm_artist(part)
        }

        lock = self._pool_locks.setdefault(context.station_id, asyncio.Lock())
        async with lock:
            pool = self._pool_for(
                context.station_id,
                seed_video_id=seed_video_id,
                pool_limit=pool_limit,
                discovery_reach=discovery_reach,
            )

            # Refresh the original seed on every refill. Besides keeping the anchor
            # current, this lets YouTube Music introduce new direct recommendations.
            root_rows = await self._radio(seed_video_id, pool_limit)
            if root_rows:
                self._merge_rows(
                    pool,
                    root_rows,
                    source_video_id=seed_video_id,
                    source_depth=0,
                    source_score=1.0,
                    seed_video_id=seed_video_id,
                    seed_title=seed_title,
                    seed_artist=seed_artist,
                    blacklist=blacklist,
                )

            # Expand at most one additional seed per refill. This makes the universe
            # grow continuously without multiplying YTM requests or making one queue
            # refill walk several graph levels in series.
            expansion = self._choose_expansion_seed(pool, discovery_reach)
            if expansion is not None:
                expansion_video_id = _clean(str(expansion.get("video_id") or ""))
                if expansion_video_id:
                    pool["expanded_video_ids"].add(expansion_video_id)
                    expansion_rows = await self._radio(expansion_video_id, pool_limit)
                    if expansion_rows:
                        self._merge_rows(
                            pool,
                            expansion_rows,
                            source_video_id=expansion_video_id,
                            source_depth=int(expansion.get("best_depth") or 1),
                            source_score=float(expansion.get("score") or 0.0),
                            seed_video_id=seed_video_id,
                            seed_title=seed_title,
                            seed_artist=seed_artist,
                            blacklist=blacklist,
                        )

            self._prune_candidates(pool, pool_limit)
            candidate_snapshot = list(pool["candidates"].values())

        if not candidate_snapshot:
            return []

        unavailable_pairs = {_pair(t.title, t.artist) for t in context.recent_tracks or []}
        unavailable_pairs.update(_pair(t.title, t.artist) for t in context.queued_tracks or [])
        unavailable_pairs.update(result.key() for result in context.already_selected or [])
        selected: list[StationResult] = []
        selected_keys: set[str] = set()
        seed_artist_norm = _norm_artist(seed_artist)

        attempts = max(12, int(count) * 10)
        for _ in range(attempts):
            if len(selected) >= max(1, int(count)):
                break
            blocked = _blocked_artists(context, selected, artist_cooldown)
            weighted: list[tuple[dict[str, Any], float]] = []
            for candidate in candidate_snapshot:
                row = candidate.get("row") or {}
                title = _clean(str(row.get("title") or ""))
                artist = _clean(str(row.get("artist") or ""))
                key = _pair(title, artist)
                if not title or not artist or key in unavailable_pairs or key in selected_keys:
                    continue
                artist_norm = _norm_artist(artist)
                if artist_norm in blocked or artist_norm in blacklist:
                    continue

                closeness = max(0.0001, min(1.0, float(candidate.get("score") or 0.0)))
                # A zero bias intentionally flattens closeness so all eligible songs
                # are nearly equal; higher values increasingly favor the anchor.
                weight = 1.0 if rank_power <= 0.0 else closeness ** rank_power
                if seed_artist_norm and artist_norm == seed_artist_norm:
                    weight *= 1.0 + (2.0 * seed_influence)
                weighted.append((candidate, max(0.0001, weight)))

            if not weighted:
                break

            chosen, _ = random.choices(
                weighted,
                weights=[item[1] for item in weighted],
                k=1,
            )[0]
            row = chosen.get("row") or {}
            title = _clean(str(row.get("title") or ""))
            artist = _clean(str(row.get("artist") or ""))
            result = StationResult(
                title=title,
                artist=artist,
                album=_clean(str(row.get("album") or "")),
                duration_ms=int(row.get("duration_ms") or 0),
                reason=f"Song Radio anchored to {seed_artist} - {seed_title}",
                confidence=max(0.0001, min(1.0, float(chosen.get("score") or 0.0))),
                provider_metadata={
                    "video_id": _clean(str(row.get("video_id") or row.get("videoId") or "")),
                    "thumbnail_url": _clean(str(row.get("thumbnail_url") or "")),
                    "seed_video_id": seed_video_id,
                    "radio_rank": int(chosen.get("best_rank") or 0),
                    "discovery_depth": int(chosen.get("best_depth") or 1),
                    "anchor_closeness": round(float(chosen.get("score") or 0.0), 6),
                    "recommendation_paths": int(chosen.get("support_count") or 1),
                    "discovery_source": "ytmusic",
                    "provider": self.station_type,
                },
            )
            selected.append(result)
            selected_keys.add(_pair(title, artist))

        return selected[: max(1, int(count))]
