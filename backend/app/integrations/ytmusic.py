from __future__ import annotations

# Consolidated YTMusic integration (merged ytmusic_api.py + ytmusic_search.py)
# - ytmusicapi-based search helpers
# - yt-dlp based best-match helpers

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import re

from ytmusicapi import YTMusic
from yt_dlp import YoutubeDL


# --- ytmusicapi search helpers ---

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

def find_song(
    title: str,
    artist: str,
    *,
    limit: int = 10,
) -> Optional[YTMusicSong]:
    """
    Search YouTube Music for a track and return the best matching YTMusicSong.

    Strongly prefers exact title + artist matches.
    Rejects bogus "views" album strings.
    """

    q_title = (title or "").strip()
    q_artist = (artist or "").strip()

    if not q_title:
        return None

    query = f"{q_artist} - {q_title}" if q_artist else q_title
    c = _client()

    try:
        results = c.search(query, filter="songs", limit=int(limit) if limit else 10) or []
    except Exception:
        return None

    best_score = -1.0
    best_song: Optional[YTMusicSong] = None

    q_title_l = q_title.lower()
    q_artist_l = q_artist.lower()

    for it in results:
        if not isinstance(it, dict):
            continue

        video_id = str(it.get("videoId") or "")
        if not video_id:
            continue

        title_res = str(it.get("title") or "").strip()

        artists_raw = it.get("artists") or []
        artist_res = ""
        if isinstance(artists_raw, list) and artists_raw:
            artist_res = str(artists_raw[0].get("name") or "").strip()

        album = ""
        album_obj = it.get("album")
        if isinstance(album_obj, dict):
            album = str(album_obj.get("name") or "").strip()

        # Guard against accidental "123K views"
        if album and "views" in album.lower():
            album = ""

        duration_seconds = _duration_to_seconds(it.get("duration"))

        thumbnails = it.get("thumbnails") or []
        thumbnail_url = ""
        if isinstance(thumbnails, list) and thumbnails:
            thumbnail_url = str(thumbnails[-1].get("url") or "")

        score = 0.0

        # Exact title match
        if title_res.lower() == q_title_l:
            score += 2.0
        elif q_title_l in title_res.lower():
            score += 1.0

        # Exact artist match
        if q_artist_l and artist_res.lower() == q_artist_l:
            score += 2.0
        elif q_artist_l and q_artist_l in artist_res.lower():
            score += 1.0

        # Bonus if album exists
        if album:
            score += 0.5

        if score > best_score:
            best_score = score
            best_song = YTMusicSong(
                video_id=video_id,
                title=title_res,
                artist=artist_res,
                album=album,
                duration_seconds=duration_seconds,
                thumbnail_url=thumbnail_url,
            )

    return best_song


# --- yt-dlp best-match helpers ---

def _norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"\s+", " ", s).strip()
    # strip punctuation (keep hyphens)
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _ytmusic_url(video_id: str) -> str:
    return f"https://music.youtube.com/watch?v={video_id}" if video_id else ""


def _youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}" if video_id else ""


@dataclass
class YTResult:
    found: bool
    confidence: float
    video_id: str = ""
    title: str = ""
    uploader: str = ""
    duration_seconds: Optional[int] = None
    album: str = ""
    artist: str = ""

    @property
    def youtube_url(self) -> str:
        return _youtube_url(self.video_id)

    @property
    def ytmusic_url(self) -> str:
        return _ytmusic_url(self.video_id)


def _score_track(
    *,
    want_title: str,
    want_artist: str,
    want_duration_s: Optional[int],
    cand_title: str,
    cand_uploader: str,
    cand_duration_s: Optional[int],
) -> float:
    want = _norm(f"{want_artist} {want_title}")
    have = _norm(f"{cand_uploader} {cand_title}")
    wt = set(want.split())
    ht = set(have.split())
    if not wt or not ht:
        base = 0.0
    else:
        base = len(wt & ht) / max(1, len(wt))

    boost = 0.0
    up = (cand_uploader or "").lower()
    if " - topic" in up or "vevo" in up or "official" in up:
        boost += 0.12

    # Duration closeness helps a lot when we have it.
    dur = 0.0
    if want_duration_s and cand_duration_s:
        diff = abs(int(want_duration_s) - int(cand_duration_s))
        if diff <= 2:
            dur += 0.25
        elif diff <= 10:
            dur += 0.12
        elif diff <= 30:
            dur += 0.04
        else:
            dur -= 0.10

    # Penalize common wrong-version indicators.
    penalty = 0.0
    t = (cand_title or "").lower()
    if any(x in t for x in ["live", "concert", "cover", "karaoke", "instrumental", "reaction"]):
        penalty -= 0.12

    return float(base + boost + dur + penalty)


def _search_yt(query: str, *, limit: int = 7) -> List[Dict[str, Any]]:
    """Fast search using yt-dlp's ytsearch.

    Returns flat entries with id/title/uploader/duration.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": True,
        "noplaylist": True,
    }
    term = f"ytsearch{int(limit)}:{query}"
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(term, download=False)
    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return []
    out: List[Dict[str, Any]] = []
    for e in entries:
        if isinstance(e, dict) and e.get("id"):
            out.append(e)
    return out


def find_track(
    *,
    title: str,
    artist: str,
    album: Optional[str] = None,
    duration_seconds: Optional[int] = None,
    limit: int = 7,
) -> YTResult:
    # Query format that tends to behave well.
    print(f"Searching for {title} by {artist}")
    q = f"{artist} - {title}"
    if album:
        q = f"{q} \"{album}\""

    cands = _search_yt(q, limit=limit)
    if not cands:
        return YTResult(found=False, confidence=0.0)

    scored: List[Tuple[float, Dict[str, Any]]] = []
    for c in cands:
        s = _score_track(
            want_title=title,
            want_artist=artist,
            want_duration_s=duration_seconds,
            cand_title=str(c.get("title") or ""),
            cand_uploader=str(c.get("uploader") or ""),
            cand_duration_s=c.get("duration") if isinstance(c.get("duration"), (int, float)) else None,
        )
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_s, best = scored[0]

    # Decide if it's "found" based on a conservative threshold.
    found = best_s >= 0.45
    return YTResult(
        found=found,
        confidence=float(best_s),
        video_id=str(best.get("id") or ""),
        title=str(best.get("title") or ""),
        uploader=str(best.get("uploader") or ""),
        duration_seconds=best.get("duration") if isinstance(best.get("duration"), (int, float)) else None,
    )


def find_album(
    *,
    album_title: str,
    artist: str,
    limit: int = 7,
) -> YTResult:
    # Album results on YT Music are often surfaced as videos ("Full Album") or auto-generated uploads.
    q = f"{artist} {album_title} full album"
    cands = _search_yt(q, limit=limit)
    if not cands:
        return YTResult(found=False, confidence=0.0)

    # Reuse track-ish scoring but with album tokens.
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for c in cands:
        s = _score_track(
            want_title=album_title,
            want_artist=artist,
            want_duration_s=None,
            cand_title=str(c.get("title") or ""),
            cand_uploader=str(c.get("uploader") or ""),
            cand_duration_s=c.get("duration") if isinstance(c.get("duration"), (int, float)) else None,
        )
        # Prefer things that explicitly look like album uploads.
        t = (str(c.get("title") or "")).lower()
        if "full album" in t or "album" in t:
            s += 0.08
        scored.append((s, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    best_s, best = scored[0]
    found = best_s >= 0.40
    return YTResult(
        found=found,
        confidence=float(best_s),
        video_id=str(best.get("id") or ""),
        title=str(best.get("title") or ""),
        uploader=str(best.get("uploader") or ""),
        duration_seconds=best.get("duration") if isinstance(best.get("duration"), (int, float)) else None,
    )
