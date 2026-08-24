from __future__ import annotations

import asyncio
import logging
import os
import random
import re
from typing import Any, Iterable

from ...integrations.listenbrainz import lb_radio_for_tags
from ...integrations.musicbrainz import _client as _musicbrainz_client, simplify_recording
from ..base import StationProvider
from ..models import StationConfigOption, StationContext, StationResult

LOG = logging.getLogger("helix.station_providers.tag_radio")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _norm(value: Any) -> str:
    text = _clean(value).lower()
    text = text.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _norm_pair(title: Any, artist: Any) -> str:
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
    return {_norm(artist) for artist in sequence[:cooldown] if _norm(artist)}

def _parse_tags(raw: Any) -> list[str]:
    if isinstance(raw, list):
        parts = [str(item or "") for item in raw]
    else:
        parts = re.split(r"[,\n]", str(raw or ""))
    tags: list[str] = []
    seen: set[str] = set()
    for part in parts:
        tag = _clean(part)
        key = _norm(tag)
        if not tag or not key or key in seen:
            continue
        tags.append(tag)
        seen.add(key)
    return tags


def _parse_artists(raw: Any) -> set[str]:
    if isinstance(raw, list):
        parts = [str(item or "") for item in raw]
    else:
        parts = re.split(r"[,\n]", str(raw or ""))
    return {_norm(part) for part in parts if _norm(part)}


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _pick_first(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, list) and value:
            value = value[0]
        text = _clean(value)
        if text:
            return text
    return ""


def _duration_ms(item: dict[str, Any]) -> int:
    raw = item.get("duration_ms") or item.get("durationMs") or item.get("duration") or item.get("length") or 0
    value = _safe_int(raw, 0)
    if 0 < value < 10_000:
        return value * 1000
    return max(0, value)


def _candidate_results(payload: Any) -> list[StationResult]:
    results: list[StationResult] = []
    seen: set[str] = set()
    for item in _walk_dicts(payload):
        title = _pick_first(item, ("recording_name", "track_name", "title", "name"))
        artist = _pick_first(item, ("artist_name", "artist", "creator", "artist_credit_name"))
        if not title or not artist:
            continue
        pair = _norm_pair(title, artist)
        if not pair or pair in seen:
            continue
        seen.add(pair)
        results.append(
            StationResult(
                title=title,
                artist=artist,
                album=_pick_first(item, ("release_name", "album", "release", "release_title")),
                duration_ms=_duration_ms(item),
                reason="ListenBrainz/MusicBrainz tag match",
                provider_metadata={
                    "provider": "listenbrainz_tag_radio",
                    "recording_mbid": _pick_first(item, ("recording_mbid", "recording_id", "identifier")),
                    "artist_mbid": _pick_first(item, ("artist_mbid", "artist_id")),
                },
            )
        )
    return results


def _musicbrainz_tag_query(tags: list[str], operator: str) -> str:
    parts = []
    for tag in tags:
        cleaned = tag.replace('"', '').strip()
        if cleaned:
            parts.append(f'tag:"{cleaned}"')
    joiner = " AND " if operator == "AND" else " OR "
    return joiner.join(parts)


async def _musicbrainz_tag_candidates(tags: list[str], operator: str, limit: int) -> list[StationResult]:
    query = _musicbrainz_tag_query(tags, operator)
    if not query:
        return []
    try:
        data = await _musicbrainz_client().search("recording", query, limit=max(1, min(100, int(limit or 25))), inc="artist-credits+releases")
    except Exception as exc:
        LOG.warning("MusicBrainz tag fallback failed tags=%r operator=%s err=%s", tags, operator, exc)
        return []

    results: list[StationResult] = []
    seen: set[str] = set()
    for rec in data.get("recordings") or []:
        if not isinstance(rec, dict):
            continue
        title, artist, album, duration_ms, _year, _release_mbid = simplify_recording(rec)
        if not title or not artist:
            continue
        pair = _norm_pair(title, artist)
        if pair in seen:
            continue
        seen.add(pair)
        results.append(
            StationResult(
                title=title,
                artist=artist,
                album=album,
                duration_ms=duration_ms,
                reason=f"MusicBrainz tag search: {', '.join(tags)}",
                provider_metadata={
                    "provider": "listenbrainz_tag_radio",
                    "fallback": "musicbrainz",
                    "recording_mbid": str(rec.get("id") or ""),
                },
            )
        )
    return results


class TagRadioProvider(StationProvider):

    station_type = "listenbrainz_tag_radio"
    display_name = "Tag Radio"
    description = "Builds stations from ListenBrainz/MusicBrainz tags such as indie rock, synthpop, folk, or video game music."
    version = "1.1.0"
    builtin = True

    def config_options(self) -> list[StationConfigOption]:
        return [
            StationConfigOption(
                key="tags",
                label="Tags",
                type="textarea",
                description="Comma- or line-separated ListenBrainz/MusicBrainz tags to use as the station seed.",
                required=True,
                default="",
            ),
            StationConfigOption(
                key="tag_match_mode",
                label="Tag match mode",
                type="select",
                description="Require any selected tag, or require all selected tags.",
                default="OR",
                choices=[
                    {"value": "OR", "label": "Any tag"},
                    {"value": "AND", "label": "All tags"},
                ],
            ),
            StationConfigOption(
                key="discovery_depth",
                label="Discovery depth",
                type="select",
                description="Controls the breadth of the candidate pool used for this tag station.",
                default="balanced",
                choices=[
                    {"value": "safe", "label": "Safe - tighter candidate pool"},
                    {"value": "balanced", "label": "Balanced"},
                    {"value": "deep", "label": "Deep - broader candidate pool"},
                ],
            ),
            StationConfigOption(
                key="popularity_begin",
                label="Popularity range start",
                type="integer",
                description="Lower bound of the ListenBrainz popularity slice, from 0 to 100.",
                default=0,
                min_value=0,
                max_value=100,
                step=1,
            ),
            StationConfigOption(
                key="popularity_end",
                label="Popularity range end",
                type="integer",
                description="Upper bound of the ListenBrainz popularity slice, from 0 to 100.",
                default=100,
                min_value=0,
                max_value=100,
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
                description="Comma- or line-separated artist names this tag station should avoid.",
                default="",
            ),
        ]

    def validate_config(self, config: dict[str, Any]) -> None:
        super().validate_config(config)
        if not _parse_tags(config.get("tags")):
            raise ValueError("Tags are required")
        begin = max(0, min(100, _safe_int(config.get("popularity_begin"), 0)))
        end = max(0, min(100, _safe_int(config.get("popularity_end"), 100)))
        if begin >= end:
            raise ValueError("Popularity range start must be lower than popularity range end")

    async def next_tracks(self, context: StationContext, count: int) -> list[StationResult]:
        cfg = context.config or {}
        self.validate_config(cfg)

        tags = _parse_tags(cfg.get("tags"))
        operator = str(cfg.get("tag_match_mode") or "OR").strip().upper()
        if operator not in {"AND", "OR"}:
            operator = "OR"
        depth = str(cfg.get("discovery_depth") or "").strip().lower()
        if depth in {"safe", "balanced", "deep"}:
            candidate_count = {"safe": 100, "balanced": 250, "deep": 750}[depth]
        else:
            # Backward compatibility for stations saved before discovery_depth existed.
            candidate_count = max(25, min(1000, _safe_int(cfg.get("candidate_count"), 250)))
        pop_begin = max(0, min(100, _safe_int(cfg.get("popularity_begin"), 0)))
        pop_end = max(0, min(100, _safe_int(cfg.get("popularity_end"), 100)))
        if pop_begin >= pop_end:
            pop_begin, pop_end = 0, 100
        recent_window = max(0, min(500, _safe_int(cfg.get("recent_track_window"), 75)))
        artist_cooldown = max(0, min(50, _safe_int(cfg.get("artist_cooldown"), 5)))
        blacklist = _parse_artists(cfg.get("artist_blacklist") or cfg.get("artist_blacklist_items"))

        candidates: list[StationResult] = []
        lb_error = ""
        try:
            payload = await asyncio.wait_for(
                lb_radio_for_tags(tags, operator=operator, count=candidate_count, pop_begin=pop_begin, pop_end=pop_end),
                timeout=float(os.getenv("HELIX_LB_TAG_RADIO_TIMEOUT_S", "15")),
            )
            candidates = _candidate_results(payload)
        except Exception as exc:
            lb_error = str(exc)
            LOG.warning("ListenBrainz Tag Radio failed tags=%r err=%s; trying MusicBrainz fallback", tags, exc)

        if not candidates:
            candidates = await _musicbrainz_tag_candidates(tags, operator, candidate_count)

        if not candidates:
            detail = "Tag Radio could not find candidates for these tags."
            if lb_error:
                detail += f" ListenBrainz error: {lb_error}"
            raise ValueError(detail)

        random.shuffle(candidates)

        recent_pairs = {_norm_pair(item.title, item.artist) for item in list(context.recent_tracks or [])[:recent_window]}
        queued_pairs = {_norm_pair(item.title, item.artist) for item in context.queued_tracks or []}
        selected_pairs = {item.key() for item in context.already_selected or []}
        used_pairs = recent_pairs | queued_pairs | selected_pairs

        wanted = max(1, int(count or 1))
        selected: list[StationResult] = []
        for result in candidates:
            blocked_artists = _blocked_artists_for_cooldown(context, selected, artist_cooldown)
            if _norm(result.artist) in blocked_artists:
                continue
            pair = _norm_pair(result.title, result.artist)
            if not pair or pair in used_pairs:
                continue
            if _norm(result.artist) in blacklist:
                continue
            selected.append(result)
            used_pairs.add(pair)
            if len(selected) >= wanted:
                break

        return selected
