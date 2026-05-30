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
import httpx

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Station, StationTag, QueueItem, PlaybackSession, ListenHistoryItem, DislikedTrack
from .integrations.listenbrainz import lb_radio_for_artist, lb_radio_for_tags, lb_similar_artists_for_artist, lb_top_recordings_for_artist
from .integrations.musicbrainz import lookup_artist_mbid_by_name, lookup_recording, simplify_recording
from .integrations.subsonic import SubsonicClient

from .integrations.ytmusic import find_song, search_ytmusic
from .validators import is_valid_yt_video_id
from .art_sources import yt_thumbnail_url, is_allowed_art_url
from .station_providers import get_station_provider
from .station_providers.models import StationContext, StationHistorySnapshot, StationQueueSnapshot, StationResult


LOG = logging.getLogger("helix.stations")


class StationSeedArtistNotFound(Exception):
    """Raised when a station's seed artist cannot be resolved to a MusicBrainz artist MBID."""

    def __init__(self, seed_artist: str):
        seed_artist = (seed_artist or "").strip()
        msg = "Seed artist not found on MusicBrainz."
        if seed_artist:
            msg = f"Seed artist not found on MusicBrainz: {seed_artist}"
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


async def select_random_track_with_artist(artist_mbid: str, limit: int = 20) -> Dict[str, Any]:
    """Select a random popular track for a given artist MBID via ListenBrainz."""
    print(f"MBID: {artist_mbid}")
    tracks = await lb_top_recordings_for_artist(artist_mbid, limit)
    if not tracks:
        raise RuntimeError("no top recordings returned")
    choice = random.choice(tracks)
    print(choice)
    return choice


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


async def _mb_lookup_artist_id_by_name(name: str) -> str:
    q = _clean(name)
    if not q:
        return ""
    url = "https://musicbrainz.org/ws/2/artist"
    # Use a conservative search, then validate the top hit so obviously-nonexistent artists
    # don't silently resolve to an unrelated artist.
    params = {"query": q, "limit": "1", "fmt": "json"}
    headers = {"User-Agent": "Helix/0.0.13 (station-mbid-lookup)"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json() or {}
    artists = data.get("artists") or []
    if not artists:
        return ""

    top = artists[0] or {}
    top_id = str(top.get("id") or "").strip()
    top_name = _clean(str(top.get("name") or ""))
    # MusicBrainz search provides a score (0-100). Require a strong match OR an exact name match.
    try:
        score = int(top.get("score") or 0)
    except Exception:
        score = 0

    q_norm = _clean(q).lower()
    name_norm = top_name.lower()

    # Accept exact match; otherwise require a high score.
    if name_norm != q_norm and score < 90:
        return ""
    return top_id



async def _mb_fetch_artist_tags(artist_id: str) -> List[Tuple[str, float]]:
    aid = (artist_id or "").strip()
    if not aid:
        return []
    url = f"https://musicbrainz.org/ws/2/artist/{aid}"
    params = {"inc": "tags", "fmt": "json"}
    headers = {"User-Agent": "Helix/0.0.13 (station-tags)"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json() or {}
    tags = data.get("tags") or []
    out: List[Tuple[str, float]] = []
    for t in tags:
        name = _clean(str(t.get("name") or ""))
        if not name:
            continue
        try:
            cnt = float(t.get("count") or 0)
        except Exception:
            cnt = 0.0
        out.append((name, cnt))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


async def ensure_station_tags(user_id: str, station_id: str) -> List[str]:
    """Ensure station has some cached tags. Returns top tag strings.

    IMPORTANT: Never holds a DB session across awaits.
    """
    # DB burst: load station + existing tags + seed fields
    db = SessionLocal()
    try:
        station = db.get(Station, station_id)
        if not station or station.user_id != user_id:
            return []
        existing = db.execute(
            select(StationTag).where(StationTag.station_id == station.id).order_by(StationTag.weight.desc()).limit(50)
        ).scalars().all()
        if existing:
            return [t.tag for t in existing if t.tag]
        seed_artist = _clean(getattr(station, "seed_artist", "") or "")
        mb_artist_id = _clean(getattr(station, "mb_artist_id", "") or "")
    finally:
        db.close()

    # External I/O (bounded): MBID lookup + tag fetch
    if not mb_artist_id and seed_artist:
        try:
            mb_artist_id = await asyncio.wait_for(
                lookup_artist_mbid_by_name(seed_artist),
                timeout=float(os.getenv("HELIX_MB_LOOKUP_TIMEOUT_S", "8")),
            )
        except asyncio.TimeoutError:
            LOG.warning("mb artist lookup timed out for %r", seed_artist)
            mb_artist_id = ""
        except Exception as e:
            LOG.warning("mb artist lookup failed for %r: %s", seed_artist, e)
            mb_artist_id = ""

        if mb_artist_id:
            # DB burst: persist MBID
            db = SessionLocal()
            try:
                station2 = db.get(Station, station_id)
                if station2 and station2.user_id == user_id:
                    station2.mb_artist_id = mb_artist_id
                    station2.updated_at = datetime.utcnow()
                    db.commit()
            finally:
                db.close()

    if not mb_artist_id:
        return []

    try:
        tags = await asyncio.wait_for(
            _mb_fetch_artist_tags(mb_artist_id),
            timeout=float(os.getenv("HELIX_MB_TAGS_TIMEOUT_S", "10")),
        )
    except asyncio.TimeoutError:
        LOG.warning("mb artist tags fetch timed out for %s", mb_artist_id)
        return []
    except Exception as e:
        LOG.warning("mb artist tags fetch failed for %s: %s", mb_artist_id, e)
        return []

    # DB burst: write station tags
    db = SessionLocal()
    try:
        station3 = db.get(Station, station_id)
        if not station3 or station3.user_id != user_id:
            return []
        # Seed tag weights into station_tags table.
        now = datetime.utcnow()
        inserted: List[StationTag] = []
        seen_tags = set()
        for t in (tags or [])[:50]:
            if isinstance(t, tuple):
                raw_tag = t[0] if len(t) > 0 else ""
                raw_weight = t[1] if len(t) > 1 else 0.0
                tag = _clean(str(raw_tag or ""))
                try:
                    w = float(raw_weight or 0.0)
                except Exception:
                    w = 0.0
            else:
                tag = _clean(str(t.get("tag") or t.get("name") or ""))
                try:
                    w = float(t.get("weight") or 0.0)
                except Exception:
                    w = 0.0
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)
            st = StationTag(station_id=station3.id, tag=tag, weight=w, updated_at=now)
            db.add(st)
            inserted.append(st)
        db.commit()
        # Return best tags by weight.
        inserted.sort(key=lambda x: float(x.weight or 0.0), reverse=True)
        return [t.tag for t in inserted if t.tag][:20]
    finally:
        db.close()


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


def _subsonic_configured(settings: Dict[str, Any]) -> bool:
    return bool(
        str(settings.get("subsonic_base_url") or "").strip()
        and str(settings.get("subsonic_username") or "").strip()
        and str(settings.get("subsonic_password") or "").strip()
    )


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
    cfg.setdefault("discovery", float(getattr(station, "discovery", 0.35) or 0.35))
    cfg.setdefault("seed_influence", float(getattr(station, "seed_influence", 0.75) or 0.75))
    cfg.setdefault("artist_cooldown", int(getattr(station, "artist_cooldown", 5) or 5))
    cfg.setdefault("artist_variety", int(getattr(station, "artist_variety", 1) or 1))
    cfg.setdefault("allow_seed_alternates", bool(int(getattr(station, "allow_seed_alternates", 0) or 0)))
    cfg.setdefault("era_start", int(getattr(station, "era_start", 0) or 0))
    cfg.setdefault("era_end", int(getattr(station, "era_end", 0) or 0))
    cfg.setdefault("popularity_bias", int(getattr(station, "popularity_bias", 50) or 50))
    cfg.setdefault("tag_strictness", int(getattr(station, "tag_strictness", 70) or 70))
    cfg.setdefault("popular_track_pool_size", int(getattr(station, "popular_track_pool_size", 10) or 10))
    cfg.setdefault("artist_blacklist", str(getattr(station, "artist_blacklist", "") or ""))
    cfg.setdefault("artist_blacklist_items", _parse_artist_list(str(getattr(station, "artist_blacklist", "") or "")))
    cfg.setdefault("temperature", float(getattr(station, "temperature", 0.9) or 0.9))
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
    if mode != "library_only" and not (subsonic_track and subsonic_track.get("id")):
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
        if thumb and is_allowed_art_url(thumb):
            qitem.art_url = thumb

    if subsonic_track and subsonic_track.get("id"):
        qitem.source = "subsonic"
        qitem.subsonic_song_id = str(subsonic_track.get("id"))
        qitem.is_playable = True
        qitem.error = ""
        try:
            sub_title = str(subsonic_track.get("title") or "").strip()
            sub_artist = str(subsonic_track.get("artist") or "").strip()
            if sub_title:
                qitem.title = sub_title
            if sub_artist:
                qitem.artist = sub_artist
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
                alb = str(song_on_yt.get("album") or "").strip()
                search_artist = qitem.artist or cleaned_artist_name
                if alb:
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
                                if athumb and is_allowed_art_url(athumb):
                                    qitem.art_url = athumb
                                break
            except Exception:
                pass
            if not (qitem.art_url or "").strip():
                thumb = yt_thumbnail_url(vid) if vid else ""
                if thumb and is_allowed_art_url(thumb):
                    qitem.art_url = thumb
    return qitem


def _build_station_context(user_id: str, station_id: str) -> tuple[StationContext, str] | None:
    db = SessionLocal()
    try:
        station = db.get(Station, station_id)
        if not station or station.user_id != user_id:
            return None
        station_type = str(getattr(station, "station_type", "") or "listenbrainz_similar_artist")
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
    if source_mode == "library_only" and not _subsonic_configured(settings):
        source_mode = "prefer_library"
    provider_count = count
    if source_mode == "library_only":
        provider_count = max(count * 10, min(100, count + 25))
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
