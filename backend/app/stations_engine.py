from __future__ import annotations

import logging
import random
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from .models import Station, StationTag, QueueItem, PlaybackSession, ListenHistoryItem, DislikedTrack
from .integrations.ytmusic_api import search_ytmusic
from .integrations.subsonic import SubsonicClient

LOG = logging.getLogger("helix.stations")


def _clean(s: str) -> str:
    return " ".join((s or "").strip().split())


def _norm(s: str) -> str:
    s = _clean(s).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"\s+", " ", s).strip()
    return s


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
            mb_artist_id = await _mb_lookup_artist_id_by_name(station.seed_artist)
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
        k = _norm(str(a or ""))
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

    blacklist = {_norm(a) for a in _parse_artist_list(str(getattr(station, "artist_blacklist", "") or ""))}

    # Recent constraints
    cooldown_artists = set(_norm(a) for a in _recent_artists(db, user_id, limit=max(artist_cooldown, 1)))
    recent_counts = _recent_artist_counts(db, user_id, window=50)

    seed_core = _norm_title_core(station.seed_title or "") if (station.seed_type or "").lower() == "track" else ""

    queries: List[str] = []
    if tags:
        # mix tags weighted by stored weights
        stored = db.execute(
            select(StationTag.tag, StationTag.weight)
            .where(StationTag.station_id == station.id)
            .order_by(StationTag.weight.desc())
            .limit(30)
        ).all()
        pool: List[str] = []
        # tag strictness influences how strongly we prefer the top tags.
        strict = tag_strictness / 100.0
        for tag, w in stored:
            if not tag:
                continue
            rep_base = float(w) * 3.0
            rep = max(1, int(round(rep_base * (1.0 + strict))))
            pool.extend([tag] * rep)
        random.shuffle(pool)
        queries.extend(pool[:10])

    # always include a comfort fallback query
    comfort_query = ""
    if (station.seed_type or "").lower() == "track" and station.seed_title and station.seed_artist:
        comfort_query = f"{station.seed_title} {station.seed_artist}".strip()
    elif station.seed_artist:
        comfort_query = station.seed_artist
    if comfort_query:
        queries.append(comfort_query)

    # Determine which query to use
    # use_tag: discoverability increases tag use; seed influence decreases tag use.
    p_tag = d * (1.0 - (seed_infl * 0.7))
    p_tag = max(0.0, min(1.0, p_tag))
    use_tag = bool(tags) and (random.random() < p_tag)
    q = ""
    if use_tag:
        q = random.choice(queries[:-1] or queries)
    else:
        q = comfort_query or (queries[-1] if queries else "")

    # Broaden a bit: for tags, add "music" keyword; for artist, allow "similar" phrasing.
    if use_tag:
        query = f"{q} music"
    else:
        query = f"{q}"

    payload = search_ytmusic(query, song_limit=25, album_limit=0)
    songs = payload.get("songs") or []
    if not songs and comfort_query:
        payload = search_ytmusic(comfort_query, song_limit=25, album_limit=0)
        songs = payload.get("songs") or []
    if not songs:
        return None

    random.shuffle(songs)

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

    for it in songs:
        title = _clean(str(it.get("title") or ""))
        artist = _clean(str(it.get("artist") or ""))
        if not title or not artist:
            continue

        na = _norm(artist)
        if na in blacklist:
            continue
        if artist_cooldown > 0 and na in cooldown_artists:
            continue

        if seed_core and (not allow_seed_alts):
            # block alternate versions of the seed track
            if _norm_title_core(title) == seed_core:
                continue

        if _norm_pair(title, artist) in recent:
            continue

        vid = _clean(str(it.get("video_id") or ""))
        if vid and f"yt:{vid}" in disliked_keys:
            continue

        # --- Scoring ---
        score = random.random() * 0.5  # controlled randomness

        # Stay close to the seed (artist match is a weak proxy for similarity)
        if station.seed_artist and na == _norm(station.seed_artist):
            score += 1.0 * seed_infl

        # Variety penalty
        score -= variety_penalty * float(recent_counts.get(na, 0))

        # Mild penalty for obvious variants
        if _looks_like_variant(title):
            score -= 0.15

        if best is None or score > best[0]:
            best = (score, it)

    if best is None:
        pick = songs[0]
        LOG.warning(
            "[station] no candidate passed filters; falling back to first result station=%s user=%s q=%r",
            station_id,
            user_id,
            query,
        )
    else:
        pick = best[1]

    title = _clean(str(pick.get("title") or ""))
    artist = _clean(str(pick.get("artist") or ""))
    album = _clean(str(pick.get("album") or ""))
    dur_s = pick.get("duration_seconds")
    duration_ms = int(dur_s * 1000) if isinstance(dur_s, int) and dur_s > 0 else 0
    art_url = _clean(str(pick.get("thumbnail_url") or ""))
    yt_video_id = _clean(str(pick.get("video_id") or ""))

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
        source="ytmusic",
    )
    qitem.yt_video_id = yt_video_id
    qitem.yt_browse_id = ""

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
