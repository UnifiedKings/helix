from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import quote


def _clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _int_or_zero(value: Any) -> int:
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def subsonic_cover_url(cover_id: str, size: int = 512) -> str:
    """Return the Helix cover-art proxy URL for a Subsonic cover id."""
    cid = _clean(cover_id)
    return f"/api/art/subsonic/{quote(cid, safe='')}?size={int(size)}" if cid else ""


def subsonic_song_to_result(song: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a Subsonic song dict into the frontend SearchSong payload."""
    cover_id = str(song.get("coverArt") or "").strip()
    return {
        "kind": "song",
        "source": "subsonic",
        "subsonic_song_id": str(song.get("id") or ""),
        "video_id": "",
        "title": str(song.get("title") or ""),
        "artist": str(song.get("artist") or ""),
        "album": str(song.get("album") or ""),
        "duration_seconds": _int_or_zero(song.get("duration")),
        "thumbnail_url": subsonic_cover_url(cover_id),
        "youtube_url": "",
        "ytmusic_url": "",
    }


def subsonic_album_to_result(album: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a Subsonic album dict into the frontend SearchAlbum payload."""
    cover_id = str(album.get("coverArt") or "").strip()
    return {
        "kind": "album",
        "source": "subsonic",
        "subsonic_album_id": str(album.get("id") or ""),
        "browse_id": "",
        "title": str(album.get("title") or album.get("name") or ""),
        "artist": str(album.get("artist") or ""),
        "year": str(album.get("year") or ""),
        "thumbnail_url": subsonic_cover_url(cover_id),
        "ytmusic_url": "",
    }


def subsonic_artist_to_result(artist: Dict[str, Any]) -> Dict[str, Any]:
    """Shape a Subsonic artist dict into the frontend SearchArtist payload."""
    artist_id = str(artist.get("id") or "")
    album_count = _int_or_zero(artist.get("albumCount"))
    if not album_count:
        album_count = len(artist.get("album") or []) if isinstance(artist.get("album"), list) else 0
    return {
        "kind": "artist",
        "source": "subsonic",
        "browse_id": artist_id,
        "artist_id": artist_id,
        "name": str(artist.get("name") or artist.get("artist") or ""),
        "album_count": album_count,
        "thumbnail_url": "",
        "art_url": "",
    }


def subsonic_songs_to_results(songs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [subsonic_song_to_result(song) for song in songs if isinstance(song, dict)]


def subsonic_albums_to_results(albums: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [subsonic_album_to_result(album) for album in albums if isinstance(album, dict)]