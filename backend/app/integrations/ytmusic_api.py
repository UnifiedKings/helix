from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ytmusicapi import YTMusic


_CLIENT: Optional[YTMusic] = None


def _client() -> YTMusic:
    """Create a singleton YTMusic client.

    ytmusicapi can work without auth headers for public search.
    """
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = YTMusic()  # unauthenticated
    return _CLIENT


@dataclass
class YTMusicSong:
    video_id: str
    title: str
    artist: str
    album: str = ""
    duration_seconds: Optional[int] = None
    thumbnail_url: str = ""

    @property
    def youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}" if self.video_id else ""

    @property
    def ytmusic_url(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}" if self.video_id else ""


@dataclass
class YTMusicAlbum:
    browse_id: str
    title: str
    artist: str
    year: str = ""
    thumbnail_url: str = ""

    @property
    def ytmusic_url(self) -> str:
        # Albums are usually browsable by browseId.
        return f"https://music.youtube.com/browse/{self.browse_id}" if self.browse_id else ""


def _best_thumb(item: Dict[str, Any]) -> str:
    thumbs = item.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        # pick largest
        best = max(thumbs, key=lambda t: int(t.get("width") or 0))
        return str(best.get("url") or "")
    return ""


def _duration_to_seconds(d: Any) -> Optional[int]:
    """ytmusicapi returns duration like '4:47' for songs."""
    if not d:
        return None
    if isinstance(d, (int, float)):
        return int(d)
    if isinstance(d, str):
        parts = d.strip().split(":")
        if not parts or not all(p.isdigit() for p in parts):
            return None
        nums = [int(p) for p in parts]
        if len(nums) == 2:
            return nums[0] * 60 + nums[1]
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


def search_ytmusic(
    query: str,
    *,
    song_limit: int = 15,
    album_limit: int = 15,
) -> Dict[str, List[Dict[str, Any]]]:
    """Search YouTube Music and return ONLY songs and albums.

    We intentionally exclude videos, playlists, community playlists, etc.
    """
    q = (query or "").strip()
    if not q:
        return {"songs": [], "albums": []}

    c = _client()

    songs_raw = c.search(q, filter="songs", limit=int(song_limit) if song_limit else 15) or []
    albums_raw = c.search(q, filter="albums", limit=int(album_limit) if album_limit else 15) or []

    songs: List[Dict[str, Any]] = []
    for it in songs_raw:
        if not isinstance(it, dict):
            continue
        vid = str(it.get("videoId") or "")
        if not vid:
            continue
        artists = it.get("artists") or []
        artist = ""
        if isinstance(artists, list) and artists:
            artist = str(artists[0].get("name") or "")
        album = ""
        alb = it.get("album")
        if isinstance(alb, dict):
            album = str(alb.get("name") or "")
        songs.append(
            {
                "kind": "song",
                "video_id": vid,
                "title": str(it.get("title") or ""),
                "artist": artist,
                "album": album,
                "duration_seconds": _duration_to_seconds(it.get("duration")),
                "thumbnail_url": _best_thumb(it),
                "youtube_url": f"https://www.youtube.com/watch?v={vid}",
                "ytmusic_url": f"https://music.youtube.com/watch?v={vid}",
            }
        )

    albums: List[Dict[str, Any]] = []
    for it in albums_raw:
        if not isinstance(it, dict):
            continue
        bid = str(it.get("browseId") or "")
        if not bid:
            continue
        artists = it.get("artists") or []
        artist = ""
        if isinstance(artists, list) and artists:
            artist = str(artists[0].get("name") or "")
        albums.append(
            {
                "kind": "album",
                "browse_id": bid,
                "title": str(it.get("title") or ""),
                "artist": artist,
                "year": str(it.get("year") or ""),
                "thumbnail_url": _best_thumb(it),
                "ytmusic_url": f"https://music.youtube.com/browse/{bid}",
            }
        )

    return {"songs": songs, "albums": albums}


def get_album_tracks(browse_id: str) -> List[Dict[str, Any]]:
    """Fetch an album tracklist from YouTube Music.

    Returns a list of track dicts with: title, artist, duration_seconds.
    """
    bid = (browse_id or "").strip()
    if not bid:
        return []

    c = _client()
    data = c.get_album(bid) or {}
    tracks_raw = data.get("tracks") or []

    tracks: List[Dict[str, Any]] = []
    for it in tracks_raw:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        artists = it.get("artists") or []
        artist = ""
        if isinstance(artists, list) and artists:
            artist = str(artists[0].get("name") or "").strip()
        dur_s = _duration_to_seconds(it.get("duration"))
        tracks.append({
            "title": title,
            "artist": artist,
            "duration_seconds": dur_s,
        })

    return tracks


def get_album_full(browse_id: str) -> Dict[str, Any]:
    """Fetch full album metadata + tracklist from YouTube Music.

    Returns a dict suitable for the Pandora-like album view.
    """
    bid = (browse_id or "").strip()
    if not bid:
        return {}

    c = _client()
    data = c.get_album(bid) or {}

    title = str(data.get("title") or "").strip()
    artists_raw = data.get("artists") or []
    artist = ""
    if isinstance(artists_raw, list) and artists_raw:
        artist = str(artists_raw[0].get("name") or "").strip()
    year = str(data.get("year") or "").strip()
    thumb = _best_thumb(data)  # album-level thumbnails

    tracks_raw = data.get("tracks") or []
    tracks: List[Dict[str, Any]] = []
    pos = 1
    for it in tracks_raw:
        if not isinstance(it, dict):
            continue
        t_title = str(it.get("title") or "").strip()
        if not t_title:
            continue
        t_artists = it.get("artists") or []
        t_artist = ""
        if isinstance(t_artists, list) and t_artists:
            t_artist = str(t_artists[0].get("name") or "").strip()
        dur_s = _duration_to_seconds(it.get("duration"))
        video_id = str(it.get("videoId") or "")

        tracks.append(
            {
                "pos": pos,
                "title": t_title,
                "artist": t_artist or artist,
                "duration_seconds": dur_s,
                "lengthMs": (dur_s * 1000) if dur_s else 0,
                "video_id": video_id,
                "ytmusic_url": f"https://music.youtube.com/watch?v={video_id}" if video_id else "",
            }
        )
        pos += 1

    return {
        "browse_id": bid,
        "title": title,
        "artist": artist,
        "year": year,
        "trackCount": len(tracks),
        "thumbnail_url": thumb,
        "ytmusic_url": f"https://music.youtube.com/browse/{bid}",
        "tracks": tracks,
    }
