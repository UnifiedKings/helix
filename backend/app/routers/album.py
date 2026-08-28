from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import get_current_user
from ..db import SessionLocal
from ..models import User
from ..settings_store import get_settings
from ..integrations.subsonic import SubsonicClient
from ..integrations.ytmusic import get_album_full, search_ytmusic


router = APIRouter(prefix="/api", tags=["album"])


def _load_settings_short() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return dict(get_settings(db) or {})
    finally:
        db.close()


def _subsonic_client_from_settings(settings: Dict[str, Any]) -> SubsonicClient | None:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        return None
    return SubsonicClient(
        base_url=base_url,
        username=username,
        password=password,
        client_name=str(settings.get("subsonic_client_name") or "Helix"),
        api_version=str(settings.get("subsonic_api_version") or "1.16.1"),
        timeout_s=int(settings.get("subsonic_timeout_s") or 20),
    )


def _subsonic_album_detail(album: Dict[str, Any]) -> Dict[str, Any]:
    album_id = str(album.get("id") or "")
    title = str(album.get("name") or album.get("title") or "")
    artist = str(album.get("artist") or "")
    cover_id = str(album.get("coverArt") or "").strip()
    art_url = f"/api/art/subsonic/{cover_id}?size=768" if cover_id else ""

    tracks = []
    raw_tracks = album.get("song") or []
    if isinstance(raw_tracks, list):
        for song in raw_tracks:
            if not isinstance(song, dict):
                continue
            song_cover_id = str(song.get("coverArt") or cover_id or "").strip()
            song_art_url = f"/api/art/subsonic/{song_cover_id}?size=512" if song_cover_id else art_url
            try:
                duration_seconds = int(song.get("duration") or 0)
            except (TypeError, ValueError):
                duration_seconds = 0
            tracks.append({
                "title": str(song.get("title") or ""),
                "artist": str(song.get("artist") or artist),
                "album": str(song.get("album") or title),
                "duration_seconds": duration_seconds,
                "duration_ms": duration_seconds * 1000 if duration_seconds else 0,
                "art_url": song_art_url,
                "thumbnail_url": song_art_url,
                "source": "subsonic",
                "subsonic_song_id": str(song.get("id") or ""),
            })

    return {
        "browse_id": album_id,
        "subsonic_album_id": album_id,
        "source": "subsonic",
        "title": title,
        "artist": artist,
        "year": album.get("year") or "",
        "thumbnail_url": art_url,
        "art_url": art_url,
        "tracks": tracks,
    }


def _norm_text(value: Any) -> str:
    """Normalize titles/artists for cross-provider comparisons."""
    raw = unicodedata.normalize("NFKD", str(value or "")).casefold()
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", raw).strip()


def _similarity(left: Any, right: Any) -> float:
    a = _norm_text(left)
    b = _norm_text(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _track_titles(rows: Iterable[Dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _norm_text(row.get("title"))
        if title:
            out.append(title)
    return out


def _album_is_complete(subsonic_album: Dict[str, Any], reference_album: Dict[str, Any]) -> bool:
    """Return True only when every reference track exists in the Subsonic album.

    Track titles are compared as a multiset so albums containing duplicate track
    names still need the correct number of copies. A narrow fuzzy fallback handles
    harmless punctuation/metadata differences between providers without allowing a
    one-track partial album to masquerade as complete.
    """
    expected = _track_titles(reference_album.get("tracks") or [])
    present = _track_titles(subsonic_album.get("song") or [])

    if not expected or not present:
        return False
    if len(present) < len(expected):
        return False

    expected_counts = Counter(expected)
    present_counts = Counter(present)
    if all(present_counts[title] >= count for title, count in expected_counts.items()):
        return True

    # Fuzzy one-to-one fallback for small provider naming differences. Consume
    # matched Subsonic rows so one local track cannot satisfy multiple expected rows.
    remaining = list(present)
    for wanted in expected:
        best_index = -1
        best_score = 0.0
        for index, candidate in enumerate(remaining):
            score = SequenceMatcher(None, wanted, candidate).ratio()
            if score > best_score:
                best_index = index
                best_score = score
        if best_index < 0 or best_score < 0.92:
            return False
        remaining.pop(best_index)
    return True



def _match_subsonic_tracks(subsonic_album: Dict[str, Any], reference_album: Dict[str, Any]) -> list[Dict[str, Any] | None]:
    """Return a one-to-one Subsonic match for each reference track, if present."""
    expected_rows = [row for row in (reference_album.get("tracks") or []) if isinstance(row, dict)]
    present_rows = [row for row in (subsonic_album.get("song") or []) if isinstance(row, dict)]
    remaining = list(enumerate(present_rows))
    matches: list[Dict[str, Any] | None] = []

    for expected in expected_rows:
        wanted_title = _norm_text(expected.get("title"))
        wanted_artist = _norm_text(expected.get("artist") or reference_album.get("artist"))
        best_pos = -1
        best_score = 0.0
        for pos, (_original_index, candidate) in enumerate(remaining):
            candidate_title = _norm_text(candidate.get("title"))
            if not wanted_title or not candidate_title:
                continue
            title_score = SequenceMatcher(None, wanted_title, candidate_title).ratio()
            candidate_artist = _norm_text(candidate.get("artist"))
            if wanted_artist and candidate_artist:
                artist_score = SequenceMatcher(None, wanted_artist, candidate_artist).ratio()
            else:
                artist_score = 1.0
            score = (title_score * 0.9) + (artist_score * 0.1)
            if title_score < 0.92:
                continue
            if score > best_score:
                best_score = score
                best_pos = pos
        if best_pos < 0:
            matches.append(None)
            continue
        _, matched = remaining.pop(best_pos)
        matches.append(matched)

    return matches


def _annotate_reference_album_with_subsonic(
    reference_album: Dict[str, Any],
    subsonic_album: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Attach album/track Subsonic availability without replacing YTMusic metadata."""
    result = dict(reference_album or {})
    tracks = [dict(row) for row in (reference_album.get("tracks") or []) if isinstance(row, dict)]
    if not subsonic_album:
        result["subsonic_complete"] = False
        result["subsonic_album_id"] = None
        result["tracks"] = tracks
        return result

    matches = _match_subsonic_tracks(subsonic_album, reference_album)
    for index, track in enumerate(tracks):
        match = matches[index] if index < len(matches) else None
        if not match:
            continue
        track["subsonic_song_id"] = str(match.get("id") or "") or None
        track["subsonic_available"] = True

    result["tracks"] = tracks
    result["subsonic_album_id"] = str(subsonic_album.get("id") or "") or None
    result["subsonic_complete"] = bool(tracks) and len(matches) == len(tracks) and all(matches)
    return result



async def _annotate_reference_album_with_verified_subsonic_tracks(
    client: Any,
    reference_album: Dict[str, Any],
    subsonic_album: Dict[str, Any] | None,
) -> Dict[str, Any]:
    """Annotate per-track Subsonic availability, verifying unmatched tracks directly.

    Album-level matching is preferred because it is cheap and preserves track order.
    If a track is still unmatched (or the partial album itself cannot be resolved),
    fall back to Subsonic's song search so a lone imported track is still marked.
    """
    result = _annotate_reference_album_with_subsonic(reference_album, subsonic_album)
    tracks = [dict(row) for row in (result.get("tracks") or []) if isinstance(row, dict)]
    if not tracks:
        result["tracks"] = tracks
        return result

    missing_indexes = [
        index for index, track in enumerate(tracks)
        if not (track.get("subsonic_song_id") or track.get("subsonic_available"))
    ]
    if not missing_indexes:
        return result

    album_artist = str(reference_album.get("artist") or "").strip()

    async def resolve(index: int):
        track = tracks[index]
        title = str(track.get("title") or "").strip()
        artist = str(track.get("artist") or album_artist).strip()
        if not title or not artist:
            return index, None
        try:
            match = await client.search_song_best(
                title=title,
                artist=artist,
                duration_ms=track.get("duration_ms"),
                album=str(reference_album.get("title") or ""),
            )
        except Exception:
            match = None
        return index, match

    resolved = await asyncio.gather(*(resolve(index) for index in missing_indexes))
    for index, match in resolved:
        if not match:
            continue
        tracks[index]["subsonic_song_id"] = str(match.get("id") or "") or None
        tracks[index]["subsonic_available"] = True

    result["tracks"] = tracks
    result["subsonic_complete"] = bool(tracks) and all(
        track.get("subsonic_song_id") or track.get("subsonic_available") for track in tracks
    )
    if result.get("subsonic_complete") and not result.get("subsonic_album_id") and subsonic_album:
        result["subsonic_album_id"] = str(subsonic_album.get("id") or "") or None
    return result

def _best_ytmusic_album_candidate(title: str, artist: str) -> Dict[str, Any] | None:
    """Resolve the most likely YTMusic album for a Subsonic album."""
    payload = search_ytmusic(
        f"{title} {artist}".strip(),
        song_limit=0,
        album_limit=8,
    ) or {}
    candidates = payload.get("albums") or []

    best: Dict[str, Any] | None = None
    best_score = 0.0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        browse_id = str(candidate.get("browse_id") or "").strip()
        if not browse_id:
            continue

        title_score = _similarity(title, candidate.get("title"))
        artist_score = _similarity(artist, candidate.get("artist")) if artist else 1.0
        # Album title is the stronger discriminator; artist still prevents same-name
        # albums by unrelated artists from being selected.
        score = (title_score * 0.72) + (artist_score * 0.28)
        if title_score < 0.84 or (artist and artist_score < 0.62):
            continue
        if score > best_score:
            best = candidate
            best_score = score

    return best


def _reference_album_for_subsonic(album: Dict[str, Any]) -> Dict[str, Any] | None:
    title = str(album.get("name") or album.get("title") or "").strip()
    artist = str(album.get("artist") or "").strip()
    if not title:
        return None

    candidate = _best_ytmusic_album_candidate(title, artist)
    if not candidate:
        return None

    browse_id = str(candidate.get("browse_id") or "").strip()
    if not browse_id:
        return None

    full = get_album_full(browse_id) or {}
    if not full or not (full.get("tracks") or []):
        return None
    return full


@router.get("/album/{album_id}")
async def album_view(
    album_id: str,
    source: str | None = Query(default=None),
    user: User = Depends(get_current_user),
):
    aid = (album_id or "").strip()
    if not aid:
        raise HTTPException(status_code=400, detail="album_id is required")

    requested_source = (source or "").strip().lower()

    if requested_source == "subsonic":
        settings = _load_settings_short()
        client = _subsonic_client_from_settings(settings)
        if client is None:
            raise HTTPException(status_code=503, detail="Subsonic is not configured.")
        try:
            album = await client.get_album(aid)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Could not load album from Subsonic: {exc}") from exc
        finally:
            try:
                await client.close()
            except Exception:
                pass
        if not album:
            raise HTTPException(status_code=404, detail="Album not found in Subsonic.")

        # A single fulfilled song can cause Subsonic/Navidrome to expose an album
        # shell containing only that track. Do not let that partial local album
        # replace the complete discovery-source album page.
        try:
            reference_album = await asyncio.to_thread(_reference_album_for_subsonic, album)
        except Exception:
            reference_album = None

        if reference_album is not None:
            annotated_reference = _annotate_reference_album_with_subsonic(reference_album, album)
            if not annotated_reference.get("subsonic_complete"):
                # Keep the complete discovery-source album view, but annotate any
                # individual tracks already present in Subsonic.
                return annotated_reference

            detail = _subsonic_album_detail(album)
            detail["subsonic_complete"] = True
            return detail

        # If completeness cannot be verified, do not claim the album is complete.
        detail = _subsonic_album_detail(album)
        detail["subsonic_complete"] = False
        return detail

    # Existing behavior remains the default for YTMusic links and old clients.
    try:
        data = get_album_full(aid)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load album from YouTube Music: {exc}") from exc
    if not data:
        raise HTTPException(status_code=404, detail="Album not found on YouTube Music.")

    # Even when the page is opened from YTMusic, annotate any tracks already in
    # Subsonic so a partial local album is visible at track level without claiming
    # the whole album is available.
    settings = _load_settings_short()
    client = _subsonic_client_from_settings(settings)
    if client is None:
        return _annotate_reference_album_with_subsonic(data, None)

    local_album = None
    try:
        try:
            candidate = await client.search_album_best(
                album=str(data.get("title") or ""),
                artist=str(data.get("artist") or ""),
            )
            if candidate:
                local_album = await client.get_album(str(candidate.get("id") or ""))
        except Exception:
            local_album = None

        return await _annotate_reference_album_with_verified_subsonic_tracks(client, data, local_album)
    finally:
        try:
            await client.close()
        except Exception:
            pass
