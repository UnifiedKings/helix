from __future__ import annotations

import logging
import random
import time
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .models import Station, StationTag, QueueItem, PlaybackSession, ListenHistoryItem, DislikedTrack
from .integrations.listenbrainz import lb_radio_for_artist, lb_radio_for_tags
from .integrations.musicbrainz_meta import lookup_artist_mbid_by_name, lookup_recording, simplify_recording
from .integrations.subsonic import SubsonicClient

LOG = logging.getLogger("helix.stations")


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
    params = {"query": q, "limit": "1", "fmt": "json"}
    headers = {"User-Agent": "Helix/0.0.13 (station-tags)"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json() or {}
    artists = data.get("artists") or []
    if not artists:
        return ""
    return str(artists[0].get("id") or "")


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


async def ensure_station_tags(db: Session, station: Station) -> List[str]:
    """Ensure station has some cached tags. Returns top tag strings."""
    existing = db.execute(
        select(StationTag).where(StationTag.station_id == station.id).order_by(StationTag.weight.desc()).limit(50)
    ).scalars().all()
    if existing:
        return [t.tag for t in existing if t.tag]

    # Resolve MB artist id if missing.
    mb_artist_id = _clean(getattr(station, "mb_artist_id", "") or "")
    if not mb_artist_id and station.seed_artist:
        try:
            mb_artist_id = await lookup_artist_mbid_by_name(station.seed_artist)
        except Exception as e:
            LOG.warning("mb artist lookup failed for %r: %s", station.seed_artist, e)
            mb_artist_id = ""
        if mb_artist_id:
            station.mb_artist_id = mb_artist_id
            station.updated_at = datetime.utcnow()
            db.commit()

    if not mb_artist_id:
        return []

    try:
        tags = await _mb_fetch_artist_tags(mb_artist_id)
    except Exception as e:
        LOG.warning("mb artist tags fetch failed for %s: %s", mb_artist_id, e)
        return []

    # Seed tag weights into station_tags table.
    # Use a simple diminishing weight curve.
    top = tags[:25]
    if not top:
        return []
    for i, (tag, cnt) in enumerate(top):
        w = max(0.1, 1.0 - (i * 0.03))
        # small bump for higher-count tags
        if cnt > 0:
            w += min(1.0, cnt / 50.0) * 0.25
        db.add(StationTag(station_id=station.id, tag=tag, weight=float(w)))
    db.commit()
    return [t for (t, _) in top]


def _recent_pairs(db: Session, user_id: str, limit: int = 150) -> set[str]:
    rows = db.execute(
        select(ListenHistoryItem.title, ListenHistoryItem.artist)
        .where(ListenHistoryItem.user_id == user_id)
        .order_by(ListenHistoryItem.created_at.desc())
        .limit(limit)
    ).all()
    return {_norm_pair(r[0] or "", r[1] or "") for r in rows}




def _recent_recording_ids(db: Session, user_id: str, limit: int = 400) -> set[str]:
    rows = db.execute(
        select(ListenHistoryItem.mb_recording_id)
        .where(ListenHistoryItem.user_id == user_id)
        .order_by(ListenHistoryItem.created_at.desc())
        .limit(limit)
    ).all()
    return {str(r[0] or '').strip() for r in rows if str(r[0] or '').strip()}


def _recent_artist_counts(db: Session, user_id: str, window: int = 50) -> Dict[str, int]:
    """Counts of normalized artist names in recent listens."""
    rows = db.execute(
        select(ListenHistoryItem.artist)
        .where(ListenHistoryItem.user_id == user_id)
        .order_by(ListenHistoryItem.created_at.desc())
        .limit(int(window))
    ).all()
    counts: Dict[str, int] = {}
    for (a,) in rows:
        k = _norm_artist(str(a or ""))
        if not k:
            continue
        counts[k] = counts.get(k, 0) + 1
    return counts


def _looks_like_variant(title: str) -> bool:
    t = _norm(title)
    return any(w in t for w in [" live", "(live", "acoustic", "remix", "mix", "cover", "karaoke", "demo", "remaster", "version"])


def _next_queue_position(db: Session, user_id: str) -> int:
    mx = db.execute(select(func.max(QueueItem.position)).where(QueueItem.session_user_id == user_id)).scalar_one_or_none()
    return int(mx or 0) + 1 if mx is not None else 0


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


async def generate_and_append_station_track(
    db: Session,
    user_id: str,
    station_id: str,
    *,
    settings: Dict[str, Any],
    advance_to_new_item: bool,
) -> Optional[QueueItem]:
    station = db.get(Station, station_id)
    if not station or station.user_id != user_id:
        return None

    tags = await ensure_station_tags(db, station)
    recent = _recent_pairs(db, user_id, limit=200)

    # Station settings
    d = float(getattr(station, "discovery", 0.35) or 0.35)  # 0..1
    d = max(0.0, min(1.0, d))
    seed_infl = float(getattr(station, "seed_influence", 0.75) or 0.75)  # 0..1
    seed_infl = max(0.0, min(1.0, seed_infl))
    artist_cooldown = int(getattr(station, "artist_cooldown", 5) or 5)
    artist_cooldown = max(0, min(50, artist_cooldown))
    artist_variety = int(getattr(station, "artist_variety", 1) or 1)
    artist_variety = max(0, min(2, artist_variety))
    allow_seed_alts = bool(int(getattr(station, "allow_seed_alternates", 0) or 0))
    tag_strictness = int(getattr(station, "tag_strictness", 70) or 70)
    tag_strictness = max(0, min(100, tag_strictness))

    blacklist = {_norm_artist(a) for a in _parse_artist_list(str(getattr(station, "artist_blacklist", "") or ""))}

    # Recent constraints
    cooldown_artists = set(_norm_artist(a) for a in _recent_artists(db, user_id, limit=max(artist_cooldown, 1)))
    # Also consider the tail of the current queue so repeated appends can't
    # queue the same artist back-to-back before history accrues.
    try:
        tail = db.execute(
            select(QueueItem.artist)
            .where(QueueItem.session_user_id == user_id)
            .order_by(QueueItem.position.desc())
            .limit(max(artist_cooldown, 1))
        ).all()
        for (a,) in tail:
            na2 = _norm_artist(str(a or ""))
            if na2:
                cooldown_artists.add(na2)
    except Exception:
        pass
    recent_counts = _recent_artist_counts(db, user_id, window=50)

    seed_core = _norm_title_core(station.seed_title or "") if (station.seed_type or "").lower() == "track" else ""

    # --- Candidate generation (ListenBrainz radio) ---

    # Map popularity_bias (0..100 popular..obscure) to a ListenBrainz popularity range (0..100).
    pop_bias = int(getattr(station, "popularity_bias", 50) or 50)
    pop_bias = max(0, min(100, pop_bias))
    # popular -> higher pop range; obscure -> lower pop range
    pop_hi = int(round(100 - (pop_bias * 0.6)))
    pop_lo = max(0, pop_hi - 30)
    pop_lo = max(0, min(100, pop_lo))
    pop_hi = max(0, min(100, pop_hi))
    if pop_lo > pop_hi:
        pop_lo, pop_hi = pop_hi, pop_lo

    # Map discoverability to LB mode. Higher discoverability => harder mode (more adventurous).
    if d < 0.34:
        lb_mode = "easy"
    elif d < 0.67:
        lb_mode = "medium"
    else:
        lb_mode = "hard"

    # Ensure MBIDs on station (semantic anchors).
    seed_type = (station.seed_type or "artist").lower().strip()
    if seed_type == "artist":
        mb_artist_id = _clean(getattr(station, "mb_artist_id", "") or "")
        if not mb_artist_id and station.seed_artist:
            try:
                mb_artist_id = await lookup_artist_mbid_by_name(station.seed_artist)
            except Exception as e:
                LOG.warning("mb artist lookup failed for %r: %s", station.seed_artist, e)
                mb_artist_id = ""
            if mb_artist_id:
                station.mb_artist_id = mb_artist_id
                station.updated_at = datetime.utcnow()
                db.commit()

        if not mb_artist_id:
            return None

        try:
            lb_payload = await lb_radio_for_artist(
                mb_artist_id,
                mode=lb_mode,
                max_similar_artists=200,
                max_recordings_per_artist=50,
                pop_begin=pop_lo,
                pop_end=pop_hi,
                cache_ttl_s=7 * 24 * 3600,
            )
        except Exception as e:
            LOG.warning("listenbrainz lb-radio/artist failed: %s", e)
            lb_payload = {}
    else:
        # Track stations: use the station's cached MB tags as seeds for LB tag radio.
        top_tags = tags[:8] if tags else []
        try:
            lb_payload = await lb_radio_for_tags(
                top_tags,
                operator="OR",
                count=300,
                pop_begin=pop_lo,
                pop_end=pop_hi,
                cache_ttl_s=2 * 24 * 3600,
            )
        except Exception as e:
            LOG.warning("listenbrainz lb-radio/tags failed: %s", e)
            lb_payload = {}

    def _extract_items(obj: Any, acc: List[Dict[str, Any]]) -> None:
        if isinstance(obj, dict):
            if "recording_mbid" in obj and (obj.get("recording_mbid") or "").strip():
                acc.append(obj)
                return
            for v in obj.values():
                _extract_items(v, acc)
        elif isinstance(obj, list):
            for v in obj:
                _extract_items(v, acc)

    lb_items: List[Dict[str, Any]] = []
    _extract_items(lb_payload, lb_items)

    if not lb_items:
        LOG.warning("[station] no listenbrainz candidates station=%s user=%s seed=%r", station_id, user_id, station.seed_artist)
        return None

    random.shuffle(lb_items)

    # Recent IDs for de-dupe
    recent_rec_ids = _recent_recording_ids(db, user_id, limit=600)

    # Pull a small set of recent dislikes (keys) for fast filtering.
    # Pull a small set of recent dislikes (keys) for fast filtering.
    disliked_keys = set(
        k for (k,) in db.execute(
            select(DislikedTrack.key)
            .where(DislikedTrack.user_id == user_id)
            .order_by(DislikedTrack.created_at.desc())
            .limit(4000)
        ).all()
        if k
    )

    # Pick the best candidate by applying hard filters, then a simple scoring model.
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    variety_penalty = {0: 0.0, 1: 0.8, 2: 1.5}[artist_variety]

    # Pick the best candidate by applying hard filters, then a simple scoring model.
    best: Optional[Tuple[float, Dict[str, Any]]] = None
    variety_penalty = {0: 0.0, 1: 0.8, 2: 1.5}[artist_variety]

    era_start = int(getattr(station, "era_start", 0) or 0)
    era_end = int(getattr(station, "era_end", 0) or 0)

    # --- Selection strategy: cheap LB filtering first, then lazy MB resolution ---
    t0 = time.time()

    def _it_title(it: Dict[str, Any]) -> str:
        return _clean(str(it.get("recording_name") or it.get("track_name") or it.get("title") or it.get("name") or ""))

    def _it_artist(it: Dict[str, Any]) -> str:
        return _clean(str(it.get("similar_artist_name") or it.get("artist_name") or it.get("artist") or ""))

    # Observability counters
    rej = {"no_mbid": 0, "recent_track": 0, "disliked": 0, "cooldown": 0, "blacklist": 0, "seed_alt": 0, "recent_pair": 0}

    # Cheap scan cap (no MB calls)
    max_fast_consider = 600
    scored: List[Tuple[float, Dict[str, Any]]] = []

    for it in lb_items[:max_fast_consider]:
        rec_mbid = _clean(str(it.get("recording_mbid") or ""))
        if not rec_mbid:
            rej["no_mbid"] += 1
            continue
        if rec_mbid in recent_rec_ids:
            rej["recent_track"] += 1
            continue
        if f"mb:{rec_mbid}" in disliked_keys:
            rej["disliked"] += 1
            continue

        title_hint = _it_title(it)
        artist_hint = _it_artist(it)
        na_hint = _norm_artist(artist_hint) if artist_hint else ""

        if na_hint and na_hint in blacklist:
            rej["blacklist"] += 1
            continue
        if artist_cooldown > 0 and na_hint and na_hint in cooldown_artists:
            rej["cooldown"] += 1
            continue
        if seed_core and (not allow_seed_alts) and title_hint and _norm_title_core(title_hint) == seed_core:
            rej["seed_alt"] += 1
            continue
        if title_hint and artist_hint and _norm_pair(title_hint, artist_hint) in recent:
            rej["recent_pair"] += 1
            continue

        # Cheap score
        score = random.random() * 0.5
        if station.seed_artist and na_hint and na_hint == _norm_artist(station.seed_artist):
            score += 1.0 * seed_infl
        if na_hint:
            score -= {0: 0.0, 1: 0.8, 2: 1.5}[artist_variety] * float(recent_counts.get(na_hint, 0))
        if title_hint and _looks_like_variant(title_hint):
            score -= 0.15
        try:
            lcount = float(it.get("total_listen_count") or 0)
            if lcount > 0:
                score += min(0.35, (lcount ** 0.5) / 2000.0)
        except Exception:
            pass

        scored.append((score, it))

    if not scored:
        LOG.warning("[station] no candidates after fast-pass filters station=%s user=%s seed=%r rej=%s", station_id, user_id, station.seed_artist, rej)
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    shortlist = [it for _, it in scored[:30]]

    # Slow pass: resolve only top-K via MusicBrainz (cached)
    mb_lookups = 0
    mb_lookup_cap = 8
    best: Optional[Tuple[float, Dict[str, Any]]] = None

    for it in shortlist:
        if mb_lookups >= mb_lookup_cap:
            break
        rec_mbid = _clean(str(it.get("recording_mbid") or ""))
        if not rec_mbid:
            continue

        try:
            rec = await lookup_recording(rec_mbid)  # minimal (artists only)
            mb_lookups += 1
        except Exception:
            continue

        title, artist, album, duration_ms, year, release_mbid = simplify_recording(rec)
        if not title or not artist:
            continue

        na = _norm_artist(artist)
        if na in blacklist:
            continue
        if artist_cooldown > 0 and na in cooldown_artists:
            continue
        if seed_core and (not allow_seed_alts) and _norm_title_core(title) == seed_core:
            continue
        if (era_start or era_end) and year:
            if era_start and year < era_start:
                continue
            if era_end and year > era_end:
                continue
        if _norm_pair(title, artist) in recent:
            continue

        # Rescore with canonical fields
        score = random.random() * 0.3
        try:
            lcount = float(it.get("total_listen_count") or 0)
            if lcount > 0:
                score += min(0.35, (lcount ** 0.5) / 2000.0)
        except Exception:
            pass
        if station.seed_artist and na == _norm_artist(station.seed_artist):
            score += 1.0 * seed_infl
        score -= {0: 0.0, 1: 0.8, 2: 1.5}[artist_variety] * float(recent_counts.get(na, 0))
        if _looks_like_variant(title):
            score -= 0.15

        if best is None or score > best[0]:
            pick = dict(it)
            pick["_title"] = title
            pick["_artist"] = artist
            pick["_album"] = album
            pick["_duration_ms"] = duration_ms
            pick["_year"] = year
            pick["_release_mbid"] = release_mbid
            best = (score, pick)

    if best is None:
        LOG.warning("[station] no candidate passed slow-pass filters station=%s user=%s seed=%r mb_lookups=%s rej=%s", station_id, user_id, station.seed_artist, mb_lookups, rej)
        return None

    pick = best[1]

    total_ms = int((time.time() - t0) * 1000)
    LOG.info("[station] select station=%s user=%s seed=%r lb_items=%d shortlist=%d mb_lookups=%d total_ms=%d rej=%s",
             station_id, user_id, station.seed_artist, len(lb_items), len(shortlist), mb_lookups, total_ms, rej)

    title = _clean(str(pick.get("_title") or ""))
    artist = _clean(str(pick.get("_artist") or ""))
    album = _clean(str(pick.get("_album") or ""))
    duration_ms = int(pick.get("_duration_ms") or 0)

    mb_recording_id = _clean(str(pick.get("recording_mbid") or ""))
    mb_artist_id = _clean(str(pick.get("similar_artist_mbid") or ""))

    # Cover art from Cover Art Archive (best-effort)
    art_url = ""
    rel = _clean(str(pick.get("_release_mbid") or ""))
    if rel:
        art_url = f"https://coverartarchive.org/release/{rel}/front-500"

    yt_video_id = ""  # found lazily during fulfillment (stream time)

    # Match to Subsonic (fast path) but do NOT prefer it.
    song = None
    client = await _subsonic_client_from_settings(settings)
    try:
        song = await client.search_song_best(title=title, artist=artist, duration_ms=duration_ms or None)
    except Exception:
        song = None
    finally:
        try:
            await client.close()
        except Exception:
            pass

    pos = _next_queue_position(db, user_id)
    qitem = QueueItem(
        session_user_id=user_id,
        position=pos,
        kind="song",
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        art_url=art_url,
        source="missing",
    )
    qitem.yt_video_id = yt_video_id
    qitem.yt_browse_id = ""
    qitem.mb_recording_id = mb_recording_id
    qitem.mb_artist_id = mb_artist_id

    if song and song.get("id"):
        qitem.source = "subsonic"
        qitem.subsonic_song_id = str(song.get("id"))
        qitem.is_playable = True
        qitem.error = ""
    else:
        qitem.subsonic_song_id = ""
        qitem.is_playable = False
        qitem.error = "NOT_IN_LIBRARY"

    db.add(qitem)

    sess = db.get(PlaybackSession, user_id)
    if sess and advance_to_new_item:
        # If we are at the end of the queue, move to the newly appended item.
        sess.current_index = pos
        sess.is_playing = True
        sess.updated_at = datetime.utcnow()
    db.commit()

    LOG.info(
        "[station] appended station=%s user=%s pick=%r - %r yt=%s src=%s cfg={cooldown:%s discover:%s seed:%s variety:%s allow_alts:%s tag_strict:%s blacklist:%s}",
        station_id,
        user_id,
        title,
        artist,
        yt_video_id,
        qitem.source,
        artist_cooldown,
        round(d * 100.0),
        round(seed_infl * 100.0),
        artist_variety,
        allow_seed_alts,
        tag_strictness,
        len(blacklist),
    )
    return qitem