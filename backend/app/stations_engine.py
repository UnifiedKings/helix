from __future__ import annotations

import logging
import asyncio
import os
import random
import time
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import traceback

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Station, QueueItem, PlaybackSession, ListenHistoryItem, DislikedTrack
from .integrations.subsonic import SubsonicClient

from .integrations.ytmusic import find_song, search_ytmusic, get_album_full
from .validators import is_valid_yt_video_id
from .art_sources import yt_thumbnail_url, is_allowed_art_url
from .station_providers import get_station_provider
from .station_providers.models import StationContext, StationHistorySnapshot, StationQueueSnapshot, StationResult


LOG = logging.getLogger("helix.stations")


class StationSeedArtistNotFound(Exception):
    """Raised when a station provider cannot resolve its configured seed artist."""

    def __init__(self, seed_artist: str):
        seed_artist = (seed_artist or "").strip()
        msg = "Seed artist could not be resolved."
        if seed_artist:
            msg = f"Seed artist could not be resolved: {seed_artist}"
        super().__init__(msg)


class StationGenerationError(Exception):
    """Raised when a station cannot generate a next item."""

    def __init__(self, detail: str, *, status_code: int = 503):
        super().__init__(detail)
        self.status_code = int(status_code)
        self.detail = str(detail)


def _subsonic_cover_url(cover_id: str, *, size: int = 512) -> str:
    cid = (cover_id or '').strip()
    if not cid:
        return ''
    return f"/api/art/subsonic/{cid}?size={int(size)}"


def _clean(s: str) -> str:
    return " ".join((s or "").strip().split())


def _is_video_thumbnail(url: str) -> bool:
    value = _clean(url).lower()
    return (
        "i.ytimg.com/vi/" in value
        or "img.youtube.com/vi/" in value
        or "/vi_webp/" in value
    )


def _usable_station_art(url: str) -> bool:
    value = _clean(url)
    return bool(value and is_allowed_art_url(value) and not _is_video_thumbnail(value))


def _norm(s: str) -> str:
    s = _clean(s).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip()
    return s

def _norm_artist(s: str) -> str:
    """Normalize artist names for anti-repetition checks.

    Goal: treat common library variants as equivalent (e.g. "The Shins" vs "Shins",
    "Shins, The", punctuation differences).
    """
    a = _norm(s)
    if not a:
        return ""
    # handle trailing ", the"
    a = re.sub(r",\s*the$", "", a).strip()
    # drop leading "the "
    a = re.sub(r"^the\s+", "", a).strip()
    # reduce punctuation to spaces for stability
    a = re.sub(r"[^a-z0-9\s]+", " ", a)
    a = re.sub(r"\s+", " ", a).strip()
    return a


def _norm_pair(title: str, artist: str) -> str:
    return f"{_norm(title)}|{_norm(artist)}"

def _infer_int(s: Any, default: int = 0) -> int:
    """Best-effort int conversion used in station payloads."""
    try:
        return int(s)
    except Exception:
        return default


def _recent_pairs(db: Session, user_id: str, limit: int = 150) -> set[str]:
    """Return a set of normalized (title|artist) pairs from recent listen history.

    Used as a hard filter to prevent immediate repeats in station generation.
    """
    rows = (
        db.execute(
            select(ListenHistoryItem.title, ListenHistoryItem.artist)
            .where(ListenHistoryItem.user_id == user_id)
            .order_by(ListenHistoryItem.created_at.desc())
            .limit(limit)
        )
        .all()
    )
    return {_norm_pair(r[0] or "", r[1] or "") for r in rows}


def _station_blacklist(station: Station) -> List[str]:
    """Parse and normalize the station's artist blacklist."""
    raw = str(getattr(station, "artist_blacklist", "") or "")
    artists = _parse_artist_list(raw)
    return [a for a in (_norm_artist(x) for x in artists) if a]


def _pick_station_artist(
    candidate_artists: List[Dict[str, Any]],
    *,
    seed_artist: str,
    seed_influence: float,
    discovery: float,
    recent_artists: List[str],
    blacklist: set[str],
) -> str:
    """Pick an artist for the next station track.

    This mirrors the older backend's behavior (prefer seed / top-ranked similar artists),
    while allowing discovery to flatten weighting and recent/blacklist to filter.
    """
    seed_n = _norm_artist(seed_artist or "")
    recent_n = [_norm_artist(a) for a in (recent_artists or []) if a]
    recent_set = set(recent_n[:50])  # soft cooldown window

    # Build candidates as (artist_name, rank)
    parsed: List[Tuple[str, int]] = []
    for it in candidate_artists or []:
        name = _clean(str(it.get("similar_artist_name") or it.get("artist_name") or it.get("artist") or ""))
        if not name:
            continue
        na = _norm_artist(name)
        if not na or na in blacklist:
            continue
        # rank: lower is better
        rank = _infer_int(it.get("rank") or it.get("similar_artist_rank") or 0, 0)
        parsed.append((name, max(0, rank)))

    # If nothing survived, fall back to the seed artist if present.
    if not parsed:
        return seed_artist or ""

    # Weighting:
    # - base: 1/(rank+1)
    # - discovery (0..1): higher => flatter (less rank bias)
    # - seed_influence (0..1): boost seed artist
    # - recent artists: downweight (not ban) unless blacklist already did
    discovery = max(0.0, min(1.0, float(discovery)))
    seed_influence = max(0.0, min(1.0, float(seed_influence)))

    weights: List[float] = []
    names: List[str] = []
    for name, rank in parsed:
        na = _norm_artist(name)
        base = 1.0 / float(rank + 1)
        # apply discovery flattening (raise to power)
        # discovery=0 => strong rank bias, discovery=1 => very flat
        power = 1.8 - (discovery * 1.4)  # 1.8..0.4
        w = base ** power

        if seed_n and na == seed_n:
            w *= 1.0 + (2.0 * seed_influence)

        if na in recent_set:
            w *= 0.15  # strong penalty but still possible

        weights.append(max(0.0001, float(w)))
        names.append(name)

    try:
        return random.choices(names, weights=weights, k=1)[0]
    except Exception:
        return random.choice(names)


def _clean_recording_name(it: Dict[str, Any]) -> str:
    return _clean(str(it.get("recording_name") or it.get("track_name") or it.get("title") or it.get("name") or ""))


def _clean_artist_name(it: Dict[str, Any]) -> str:
    return _clean(str(it.get("similar_artist_name") or it.get("artist_name") or it.get("artist") or ""))


def find_song_on_yt(title: str, artist: str):
    """Compatibility wrapper from older backend."""
    return find_song(title=title, artist=artist)



def _best_yt_song_result(title: str, artist: str) -> Optional[Dict[str, Any]]:
    """Best-effort YT Music song lookup with richer metadata for station items."""
    q_title = _clean(title)
    q_artist = _clean(artist)
    if not q_title or not q_artist:
        return None
    try:
        res = search_ytmusic(f"{q_title} {q_artist}", song_limit=10, album_limit=5) or {}
    except Exception:
        return None

    songs = res.get("songs") or []
    if not songs:
        return None

    want_title = _norm_title_core(q_title)
    want_artist = _norm_artist(q_artist)
    best_score = float("-inf")
    best_item: Optional[Dict[str, Any]] = None

    for s in songs:
        if not isinstance(s, dict):
            continue
        vid = str(s.get("video_id") or "").strip()
        if not vid:
            continue
        cand_title = _norm_title_core(str(s.get("title") or ""))
        cand_artist = _norm_artist(str(s.get("artist") or ""))
        score = 0.0
        if cand_title == want_title:
            score += 100.0
        elif want_title and cand_title and (want_title in cand_title or cand_title in want_title):
            score += 60.0
        if cand_artist == want_artist:
            score += 80.0
        elif want_artist and cand_artist and (want_artist in cand_artist or cand_artist in want_artist):
            score += 40.0
        # Prefer richer entries.
        if str(s.get("album") or "").strip():
            score += 10.0
        if str(s.get("thumbnail_url") or "").strip():
            score += 5.0
        if score > best_score:
            best_score = score
            best_item = s

    return best_item if best_score >= 100.0 else None

def _norm_title_core(title: str) -> str:
    """Normalize a title for 'same song different version' comparisons.

    Aggressively strips common suffixes and bracketed qualifiers.
    """
    t = _clean(title).lower()
    # strip bracketed qualifiers
    t = re.sub(r"\([^\)]*\)", " ", t)
    t = re.sub(r"\[[^\]]*\]", " ", t)
    # strip common separators/suffixes
    t = re.sub(r"\s+-\s+.*$", " ", t)
    # strip feat.
    t = re.sub(r"\bfeat\.?\b.*$", " ", t)
    # remove common version words
    t = re.sub(r"\b(remaster(ed)?|live|acoustic|demo|mix|remix|cover|karaoke|version|edit|session)\b", " ", t)
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _parse_artist_list(s: str) -> List[str]:
    raw = (s or "").replace("\r", "\n")
    parts: List[str] = []
    for line in raw.split("\n"):
        for p in line.split(","):
            p = _clean(p)
            if p:
                parts.append(p)
    # de-dupe while preserving order
    seen = set()
    out: List[str] = []
    for p in parts:
        k = _norm(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def _recent_artists(db: Session, user_id: str, limit: int = 60) -> List[str]:
    rows = db.execute(
        select(ListenHistoryItem.artist)
        .where(ListenHistoryItem.user_id == user_id)
        .order_by(ListenHistoryItem.created_at.desc())
        .limit(limit)
    ).all()
    return [_clean(r[0] or "") for r in rows if _clean(r[0] or "")]


async def _subsonic_client_from_settings(settings: Dict[str, Any]) -> SubsonicClient:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        raise RuntimeError("Subsonic settings incomplete")
    client_name = str(settings.get("subsonic_client_name") or "Helix")
    api_version = str(settings.get("subsonic_api_version") or "1.16.1")
    timeout_s = int(settings.get("subsonic_timeout_s") or 20)
    return SubsonicClient(base_url=base_url, username=username, password=password, client_name=client_name, api_version=api_version, timeout_s=timeout_s)


async def match_track_to_subsonic(*, settings: Dict[str, Any], title: str, artist: str, duration_ms: Optional[int]) -> Optional[Dict[str, Any]]:
    """Resolve a (title, artist) pair to a Subsonic song dict if possible.

    NOTE: Some Subsonic-compatible servers omit fields like `coverArt` / `album` from `search3` song results.
    For station playback we want Subsonic-backed items to look identical to direct-play items, so we
    best-effort hydrate missing fields via `getSong.view`.
    """
    try:
        client = await _subsonic_client_from_settings(settings)
    except Exception:
        return None
    try:
        best = await client.search_song_best(title=title, artist=artist, duration_ms=duration_ms)
        if not best or not best.get("id"):
            return best

        # Best-effort hydration: if search result lacks album/coverArt, fetch full song details.
        if not (best.get("coverArt") and str(best.get("coverArt")).strip()) or not (best.get("album") and str(best.get("album")).strip()):
            try:
                full = await client.get_song(str(best.get("id")))
                if isinstance(full, dict):
                    # only fill missing keys; don't overwrite search3 values that are present
                    for k in ("album", "albumId", "coverArt", "duration", "artist", "title"):
                        v = full.get(k)
                        if v is None:
                            continue
                        if k not in best or best.get(k) in (None, "", 0):
                            best[k] = v
            except Exception:
                pass

        return best
    finally:
        try:
            await client.close()
        except Exception:
            pass



def _station_config_from_model(station: Station) -> dict[str, Any]:
    """Build provider config from the station row.

    Existing Station columns are mirrored into config so old stations keep working
    while new provider-specific config lives in config_json.
    """
    try:
        import json
        cfg = json.loads(str(getattr(station, "config_json", "{}") or "{}"))
        if not isinstance(cfg, dict):
            cfg = {}
    except Exception:
        cfg = {}

    cfg.setdefault("seed_type", getattr(station, "seed_type", "artist") or "artist")
    cfg.setdefault("seed_title", getattr(station, "seed_title", "") or "")
    cfg.setdefault("seed_artist", getattr(station, "seed_artist", "") or "")
    cfg.setdefault("mb_artist_id", getattr(station, "mb_artist_id", "") or "")
    cfg.setdefault("mb_recording_id", getattr(station, "mb_recording_id", "") or "")
    cfg.setdefault("discovery", float(0.35 if getattr(station, "discovery", None) is None else getattr(station, "discovery")))
    cfg.setdefault("seed_influence", float(0.75 if getattr(station, "seed_influence", None) is None else getattr(station, "seed_influence")))
    cfg.setdefault("artist_cooldown", int(getattr(station, "artist_cooldown", 5) if getattr(station, "artist_cooldown", None) is not None else 5))
    cfg.setdefault("artist_variety", int(1 if getattr(station, "artist_variety", None) is None else getattr(station, "artist_variety")))
    cfg.setdefault("allow_seed_alternates", bool(int(getattr(station, "allow_seed_alternates", 0) or 0)))
    cfg.setdefault("era_start", int(getattr(station, "era_start", 0) or 0))
    cfg.setdefault("era_end", int(getattr(station, "era_end", 0) or 0))
    cfg.setdefault("popularity_bias", int(50 if getattr(station, "popularity_bias", None) is None else getattr(station, "popularity_bias")))
    cfg.setdefault("tag_strictness", int(70 if getattr(station, "tag_strictness", None) is None else getattr(station, "tag_strictness")))
    cfg.setdefault("popular_track_pool_size", int(10 if getattr(station, "popular_track_pool_size", None) is None else getattr(station, "popular_track_pool_size")))
    cfg.setdefault("artist_blacklist", str(getattr(station, "artist_blacklist", "") or ""))
    cfg.setdefault("artist_blacklist_items", _parse_artist_list(str(getattr(station, "artist_blacklist", "") or "")))
    cfg.setdefault("temperature", float(0.9 if getattr(station, "temperature", None) is None else getattr(station, "temperature")))
    cfg.setdefault("source_mode", "prefer_library")
    return cfg


def _station_source_mode(config: dict[str, Any] | None) -> str:
    mode = str((config or {}).get("source_mode") or "prefer_library").strip().lower()
    if mode in {"library", "library_only", "subsonic", "subsonic_only"}:
        return "library_only"
    return "prefer_library"


def _queue_item_from_result(choice: StationResult) -> QueueItem:
    return QueueItem(
        position=0,
        kind="station",
        title=_clean(choice.title) or "(Station Track)",
        artist=_clean(choice.artist),
        album=_clean(choice.album),
        duration_ms=_infer_int(choice.duration_ms or 0, 0),
        art_url="",
    )


async def _resolve_station_result_to_queue_item(choice: StationResult, *, settings: Dict[str, Any], source_mode: str = "prefer_library") -> Optional[QueueItem]:
    """Resolve a source-neutral provider recommendation to a playable queue item.

    source_mode="library_only" prevents YTMusic fallback/download fulfillment.
    """
    cleaned_track_name = _clean(choice.title)
    cleaned_artist_name = _clean(choice.artist)
    if not cleaned_track_name or not cleaned_artist_name:
        return None

    try:
        subsonic_track = await asyncio.wait_for(
            match_track_to_subsonic(settings=settings, title=cleaned_track_name, artist=cleaned_artist_name, duration_ms=choice.duration_ms or None),
            timeout=float(os.getenv("HELIX_SUBSONIC_SEARCH_TIMEOUT_S", "10")),
        )
    except asyncio.TimeoutError:
        subsonic_track = None

    mode = _station_source_mode({"source_mode": source_mode})
    if mode == "library_only" and not (subsonic_track and subsonic_track.get("id")):
        return None

    song_on_yt = None
    if mode != "library_only":
        provider_meta = choice.provider_metadata if isinstance(choice.provider_metadata, dict) else {}
        provider_video_id = _clean(str(provider_meta.get("video_id") or provider_meta.get("videoId") or ""))
        if str(provider_meta.get("discovery_source") or "").strip().lower() == "ytmusic" and provider_video_id:
            provider_duration_s = int(choice.duration_ms / 1000) if choice.duration_ms else 0
            provider_album = _clean(choice.album)
            provider_album_browse_id = _clean(str(
                provider_meta.get("album_browse_id")
                or provider_meta.get("albumBrowseId")
                or provider_meta.get("album_id")
                or ""
            ))
            provider_thumb = _clean(str(provider_meta.get("thumbnail_url") or ""))
            if _is_video_thumbnail(provider_thumb):
                provider_thumb = ""

            # Artist-page recommendations often give us an exact video ID but
            # incomplete metadata.  Previously we only enriched the result when
            # duration was missing, which meant station tracks with a duration
            # but no proper YTMusic artwork fell through to hqdefault.jpg.
            #
            # Keep the provider's exact video ID, but also enrich when artwork is
            # absent (or is only a generic YouTube video thumbnail).  Matching by
            # video ID avoids accidentally substituting a different recording.
            provider_thumb_is_video = (
                "i.ytimg.com/vi/" in provider_thumb.lower()
                or "img.youtube.com/vi/" in provider_thumb.lower()
            )
            if provider_duration_s <= 0 or not provider_thumb or provider_thumb_is_video or not provider_album:
                try:
                    lookup = await asyncio.wait_for(
                        asyncio.to_thread(
                            search_ytmusic,
                            f"{cleaned_track_name} {cleaned_artist_name}",
                            song_limit=10,
                            album_limit=0,
                        ),
                        timeout=float(os.getenv("HELIX_YTMUSIC_METADATA_TIMEOUT_S", "8")),
                    )

                    exact_video_candidate = None
                    for candidate in list((lookup or {}).get("songs") or []):
                        if _clean(str(candidate.get("video_id") or "")) == provider_video_id:
                            exact_video_candidate = candidate
                            break

                    # Keep the provider's exact video id for playback, but do
                    # not require the metadata result to use that same id.
                    # YTMusic stations frequently recommend a watch/video id
                    # while normal Search resolves the canonical song entry.
                    # Search has the album art we actually want, so metadata is
                    # allowed to come from the best title+artist match.
                    metadata_candidate = exact_video_candidate
                    if not metadata_candidate:
                        metadata_candidate = _best_yt_song_result(
                            cleaned_track_name,
                            cleaned_artist_name,
                        )

                    if metadata_candidate:
                        candidate_duration_s = _infer_int(
                            metadata_candidate.get("duration_seconds") or 0,
                            0,
                        )
                        if provider_duration_s <= 0 and candidate_duration_s > 0:
                            provider_duration_s = candidate_duration_s

                        provider_album = (
                            _clean(str(metadata_candidate.get("album") or ""))
                            or provider_album
                        )
                        provider_album_browse_id = (
                            _clean(str(metadata_candidate.get("album_browse_id") or ""))
                            or provider_album_browse_id
                        )
                        candidate_thumb = _clean(
                            str(metadata_candidate.get("thumbnail_url") or "")
                        )
                        if _usable_station_art(candidate_thumb):
                            provider_thumb = candidate_thumb
                except Exception:
                    pass

            # Even when the provider already supplied duration/album metadata,
            # it may still have no usable artwork. In that case always consult
            # the same title+artist search path used by the Search page.
            if not _usable_station_art(provider_thumb):
                try:
                    metadata_candidate = await asyncio.wait_for(
                        asyncio.to_thread(
                            _best_yt_song_result,
                            cleaned_track_name,
                            cleaned_artist_name,
                        ),
                        timeout=float(os.getenv("HELIX_YTMUSIC_METADATA_TIMEOUT_S", "8")),
                    )
                    if metadata_candidate:
                        provider_album = (
                            _clean(str(metadata_candidate.get("album") or ""))
                            or provider_album
                        )
                        provider_album_browse_id = (
                            _clean(str(metadata_candidate.get("album_browse_id") or ""))
                            or provider_album_browse_id
                        )
                        candidate_thumb = _clean(
                            str(metadata_candidate.get("thumbnail_url") or "")
                        )
                        if _usable_station_art(candidate_thumb):
                            provider_thumb = candidate_thumb
                except Exception:
                    pass

            # If exact song search did not include an album browse id, resolve
            # the album by exact title + artist before giving up.
            if provider_album and not provider_album_browse_id:
                try:
                    album_lookup = await asyncio.wait_for(
                        asyncio.to_thread(
                            search_ytmusic,
                            f"{cleaned_artist_name} {provider_album}",
                            song_limit=0,
                            album_limit=12,
                        ),
                        timeout=float(os.getenv("HELIX_YTMUSIC_METADATA_TIMEOUT_S", "8")),
                    )
                    want_album = _norm(provider_album)
                    want_artist = _norm(cleaned_artist_name)
                    for album_candidate in list((album_lookup or {}).get("albums") or []):
                        candidate_title = _norm(str(album_candidate.get("title") or ""))
                        candidate_artist = _norm(str(album_candidate.get("artist") or ""))
                        if candidate_title != want_album:
                            continue
                        if want_artist and candidate_artist and not (
                            candidate_artist == want_artist
                            or want_artist in candidate_artist
                            or candidate_artist in want_artist
                        ):
                            continue
                        provider_album_browse_id = _clean(str(album_candidate.get("browse_id") or ""))
                        candidate_album_thumb = _clean(str(album_candidate.get("thumbnail_url") or ""))
                        if _usable_station_art(candidate_album_thumb):
                            provider_thumb = candidate_album_thumb
                        break
                except Exception:
                    pass

            # If the exact YTMusic song tells us which album it belongs to,
            # resolve that album directly.
            if provider_album_browse_id:
                try:
                    album_full = await asyncio.wait_for(
                        asyncio.to_thread(get_album_full, provider_album_browse_id),
                        timeout=float(os.getenv("HELIX_YTMUSIC_ALBUM_TIMEOUT_S", "8")),
                    )
                    album_thumb = _clean(str((album_full or {}).get("thumbnail_url") or ""))
                    if _usable_station_art(album_thumb):
                        provider_thumb = album_thumb
                    provider_album = _clean(str((album_full or {}).get("title") or "")) or provider_album
                except Exception:
                    pass

            song_on_yt = {
                "video_id": provider_video_id,
                "title": cleaned_track_name,
                "artist": cleaned_artist_name,
                "album": provider_album,
                "album_browse_id": provider_album_browse_id,
                "duration_seconds": provider_duration_s,
                "thumbnail_url": provider_thumb,
            }
        else:
            try:
                song_on_yt = _best_yt_song_result(cleaned_track_name, cleaned_artist_name)
            except Exception:
                song_on_yt = None

    if not (subsonic_track and subsonic_track.get("id")) and not song_on_yt:
        return None

    qitem = _queue_item_from_result(choice)

    if song_on_yt:
        qitem.title = _clean(str(song_on_yt.get("title") or "")) or qitem.title
        qitem.artist = _clean(str(song_on_yt.get("artist") or "")) or qitem.artist
        qitem.album = _clean(str(song_on_yt.get("album") or "")) or qitem.album
        yt_duration_s = _infer_int(song_on_yt.get("duration_seconds") or 0, 0)
        if yt_duration_s > 0:
            qitem.duration_ms = yt_duration_s * 1000
        thumb = str(song_on_yt.get("thumbnail_url") or "").strip()
        if _usable_station_art(thumb):
            qitem.art_url = thumb

    if subsonic_track and subsonic_track.get("id"):
        qitem.source = "subsonic"
        qitem.subsonic_song_id = str(subsonic_track.get("id"))
        qitem.is_playable = True
        qitem.error = ""
        try:
            alb = str(subsonic_track.get("album") or "").strip()
            if alb:
                qitem.album = alb
            sub_duration_s = _infer_int(subsonic_track.get("duration") or 0, 0)
            if sub_duration_s > 0:
                qitem.duration_ms = sub_duration_s * 1000
            cover_id = str(subsonic_track.get("coverArt") or "").strip()
            if cover_id:
                qitem.art_url = _subsonic_cover_url(cover_id)
        except Exception:
            pass

        # Station discovery originates from YTMusic. If the exact YTMusic song
        # resolved to an album, prefer that album artwork even when playback will
        # come from the local Subsonic copy. This keeps Search and Station
        # artwork visually consistent.
        if song_on_yt:
            station_thumb = _clean(str(song_on_yt.get("thumbnail_url") or ""))
            if _usable_station_art(station_thumb):
                qitem.art_url = station_thumb
    else:
        qitem.source = "ytmusic"
        qitem.subsonic_song_id = ""
        qitem.is_playable = False
        qitem.error = "NOT_IN_LIBRARY"
        if song_on_yt:
            vid = str(song_on_yt.get("video_id") or "").strip()
            if vid:
                qitem.yt_video_id = vid
            try:
                known_album_browse_id = _clean(str(song_on_yt.get("album_browse_id") or ""))
                if known_album_browse_id:
                    qitem.yt_browse_id = known_album_browse_id

                alb = str(song_on_yt.get("album") or "").strip()
                search_artist = qitem.artist or cleaned_artist_name
                if alb and not known_album_browse_id:
                    res = search_ytmusic(f"{search_artist} {alb}", song_limit=0, album_limit=10) or {}
                    albums = res.get("albums") or []
                    alb_n = _norm(alb)
                    art_n = _norm(search_artist)
                    for a in albums:
                        t = _norm(str(a.get("title") or ""))
                        ar = _norm(str(a.get("artist") or ""))
                        if alb_n and t and (alb_n == t or alb_n in t or t in alb_n):
                            if not art_n or not ar or ar == art_n or art_n in ar or ar in art_n:
                                bid = str(a.get("browse_id") or "").strip()
                                if bid:
                                    qitem.yt_browse_id = bid
                                athumb = str(a.get("thumbnail_url") or "").strip()
                                if bid:
                                    try:
                                        full_album = get_album_full(bid) or {}
                                        full_thumb = _clean(str(full_album.get("thumbnail_url") or ""))
                                        if _usable_station_art(full_thumb):
                                            qitem.art_url = full_thumb
                                        elif _usable_station_art(athumb):
                                            qitem.art_url = athumb
                                    except Exception:
                                        if _usable_station_art(athumb):
                                            qitem.art_url = athumb
                                elif _usable_station_art(athumb):
                                    qitem.art_url = athumb
                                break
            except Exception:
                pass
            # Never use generic YouTube video frames for station artwork.
            if _is_video_thumbnail(qitem.art_url):
                qitem.art_url = ""

    # Final station-only recovery: mirror the normal Search-page metadata lookup
    # if every earlier provider/album path failed. Playback identity remains the
    # provider's exact video id; only artwork/album metadata comes from Search.
    if not _usable_station_art(qitem.art_url):
        try:
            fallback_meta = await asyncio.wait_for(
                asyncio.to_thread(
                    _best_yt_song_result,
                    cleaned_track_name,
                    cleaned_artist_name,
                ),
                timeout=float(os.getenv("HELIX_YTMUSIC_METADATA_TIMEOUT_S", "8")),
            )
            if fallback_meta:
                fallback_thumb = _clean(str(fallback_meta.get("thumbnail_url") or ""))
                if _usable_station_art(fallback_thumb):
                    qitem.art_url = fallback_thumb
                if not _clean(qitem.album):
                    qitem.album = _clean(str(fallback_meta.get("album") or ""))
                fallback_browse = _clean(str(fallback_meta.get("album_browse_id") or ""))
                if fallback_browse and not _clean(qitem.yt_browse_id):
                    qitem.yt_browse_id = fallback_browse
        except Exception:
            pass

    if _is_video_thumbnail(qitem.art_url):
        qitem.art_url = ""
    return qitem


def _build_station_context(user_id: str, station_id: str) -> tuple[StationContext, str] | None:
    db = SessionLocal()
    try:
        station = db.get(Station, station_id)
        if not station or station.user_id != user_id:
            return None
        station_type = str(getattr(station, "station_type", "") or "similar_artist")
        config = _station_config_from_model(station)
        recent_rows = db.execute(
            select(ListenHistoryItem.title, ListenHistoryItem.artist, ListenHistoryItem.album, ListenHistoryItem.source)
            .where(ListenHistoryItem.user_id == user_id)
            .order_by(ListenHistoryItem.created_at.desc())
            .limit(200)
        ).all()
        recent_tracks = [
            StationHistorySnapshot(title=_clean(r[0] or ""), artist=_clean(r[1] or ""), album=_clean(r[2] or ""), source=_clean(r[3] or ""))
            for r in recent_rows
            if _clean(r[0] or "") and _clean(r[1] or "")
        ]
        recent_artists = _recent_artists(db, user_id, limit=200)
        queue_rows = db.execute(
            select(QueueItem.title, QueueItem.artist, QueueItem.album, QueueItem.source, QueueItem.position)
            .where(QueueItem.session_user_id == user_id)
            .order_by(QueueItem.position.asc())
            .limit(500)
        ).all()
        queued_tracks = [
            StationQueueSnapshot(
                title=_clean(r[0] or ""),
                artist=_clean(r[1] or ""),
                album=_clean(r[2] or ""),
                source=_clean(r[3] or ""),
                position=_infer_int(r[4] or 0, 0),
            )
            for r in queue_rows
        ]
        return StationContext(
            user_id=user_id,
            station_id=station_id,
            station_name=station.name,
            station_type=station_type,
            config=config,
            recent_tracks=recent_tracks,
            recent_artists=recent_artists,
            queued_tracks=queued_tracks,
        ), station_type
    finally:
        db.close()


def _append_station_queue_item(user_id: str, qitem: QueueItem, *, advance_to_new_item: bool, position: int | None = None) -> Optional[QueueItem]:
    db = SessionLocal()
    try:
        sess = db.get(PlaybackSession, user_id)
        if not sess:
            sess = PlaybackSession(user_id=user_id)
            db.add(sess)
            db.commit()
            db.refresh(sess)
        qitem.session_user_id = user_id
        if position is None:
            max_pos = db.execute(
                select(QueueItem.position)
                .where(QueueItem.session_user_id == user_id)
                .order_by(QueueItem.position.desc())
                .limit(1)
            ).scalar_one_or_none()
            qitem.position = int(max_pos or -1) + 1
        else:
            qitem.position = int(position)
        db.add(qitem)
        if advance_to_new_item:
            sess.current_index = qitem.position
            sess.is_playing = True
        sess.updated_at = datetime.utcnow()
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        db.refresh(qitem)
        return qitem
    finally:
        db.close()


async def generate_and_append_station_tracks(
    user_id: str,
    station_id: str,
    *,
    settings: Dict[str, Any],
    count: int,
    advance_to_new_item: bool,
    positions: list[int] | None = None,
) -> list[QueueItem]:
    count = max(1, int(count or 1))
    ctx_tuple = _build_station_context(user_id, station_id)
    if not ctx_tuple:
        return []
    context, station_type = ctx_tuple
    provider = get_station_provider(station_type)
    source_mode = _station_source_mode(context.config)
    # Providers return source-neutral recommendations. Resolving those candidates to
    # playable queue items can fail (not in Subsonic, poor external-search match,
    # missing source id, etc.). Do not ask for only the exact number we need; a
    # single unresolvable candidate would make station start fail with 503.
    #
    # This does not bulk-download anything. We still append only `count` queue
    # items, and fulfillment only happens later when a track reaches the front of
    # the queue.
    # Keep a modest fallback pool without making network-backed providers build
    # dozens of recommendations for every single queue slot. Discovery results
    # with stable source ids are normally playable directly, so 4x/+4 gives the
    # resolver room to skip bad candidates while keeping next-track latency low.
    provider_count = max(count * 4, count + 4)
    if source_mode == "library_only":
        # Library-only mode needs more overfetch because many discovery candidates
        # legitimately will not exist in Subsonic, but still cap the work well below
        # the previous 20x/+40 behavior.
        provider_count = max(provider_count, count * 12, min(60, count + 16))
    try:
        results = await asyncio.wait_for(
            provider.next_tracks(context, provider_count),
            timeout=float(os.getenv("HELIX_STATION_PROVIDER_TIMEOUT_S", "20")),
        )
    except ValueError as exc:
        if "mbid" in str(exc).lower() or "seed artist" in str(exc).lower():
            raise StationSeedArtistNotFound(str(exc)) from exc
        raise StationGenerationError(str(exc), status_code=400) from exc
    except asyncio.TimeoutError as exc:
        raise StationGenerationError(f"Station provider timed out: {provider.station_type}", status_code=504) from exc

    if not results:
        raise StationGenerationError(f"Station provider returned no candidates: {provider.station_type}", status_code=503)

    appended: list[QueueItem] = []
    seen: set[str] = set()
    skipped_unplayable = 0
    for idx, choice in enumerate(results or []):
        if not choice or not _clean(choice.title) or not _clean(choice.artist):
            continue
        key = choice.key()
        if key in seen:
            continue
        seen.add(key)
        qitem = await _resolve_station_result_to_queue_item(choice, settings=settings, source_mode=source_mode)
        if not qitem:
            skipped_unplayable += 1
            continue
        target_position = positions[len(appended)] if positions and len(appended) < len(positions) else None
        saved = _append_station_queue_item(
            user_id,
            qitem,
            advance_to_new_item=advance_to_new_item and not appended,
            position=target_position,
        )
        if saved:
            appended.append(saved)
            LOG.info(
                "[station] appended station=%s user=%s provider=%s pick=%r - %r src=%s",
                station_id,
                user_id,
                provider.station_type,
                saved.title,
                saved.artist,
                saved.source,
            )
            if len(appended) >= count:
                break
    if not appended and skipped_unplayable:
        source_hint = " Library-only mode may be filtering out all candidates." if source_mode == "library_only" else ""
        raise StationGenerationError(f"Station candidates were found, but none resolved to playable tracks.{source_hint}", status_code=503)
    return appended


async def generate_and_append_station_track(user_id: str, station_id: str, *, settings: Dict[str, Any], advance_to_new_item: bool, position: int | None = None) -> Optional[QueueItem]:
    positions = [position] if position is not None else None
    items = await generate_and_append_station_tracks(
        user_id,
        station_id,
        settings=settings,
        count=1,
        advance_to_new_item=advance_to_new_item,
        positions=positions,
    )
    return items[0] if items else None
