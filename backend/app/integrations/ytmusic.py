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
        best = max(thumbs, key=lambda t: int((t or {}).get("width") or 0))
        return str((best or {}).get("url") or "")
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



def _looks_like_viewcount_or_duration(s: str) -> bool:
    """Heuristic guard: some YTMusic entries can have non-artist strings in the
    'artists' field (e.g., '1,234,567 views', '14M plays'). We treat those as invalid
    artist names.
    """
    t = (s or "").strip().lower()
    if not t:
        return True
    if "view" in t or "play" in t:
        return True
    if t in {"songs", "song", "album", "single", "ep", "video", "videos", "listeners", "subscribers"}:
        return True
    if re.fullmatch(r"[0-9][0-9,\.\s]*", t):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", t):
        return True
    return False


def _safe_artist_name(primary: str, fallback: str = "") -> str:
    a = (primary or "").strip()
    if _looks_like_viewcount_or_duration(a):
        return (fallback or "").strip()
    return a


def _section_results(section: Any) -> List[Dict[str, Any]]:
    if isinstance(section, dict):
        rows = section.get("results") or section.get("items") or []
        if isinstance(rows, list):
            return [r for r in rows if isinstance(r, dict)]
    if isinstance(section, list):
        return [r for r in section if isinstance(r, dict)]
    return []


def _artist_name_from_item(it: Dict[str, Any], fallback: str = "") -> str:
    artists = it.get("artists") or []
    if isinstance(artists, list) and artists:
        for a in artists:
            if isinstance(a, dict):
                nm = _safe_artist_name(str(a.get("name") or ""), "")
                if nm:
                    return nm
            else:
                nm = _safe_artist_name(str(a or ""), "")
                if nm:
                    return nm
    return _safe_artist_name(str(it.get("artist") or it.get("name") or ""), fallback)


def search_ytmusic(
    query: str,
    *,
    song_limit: int = 15,
    album_limit: int = 15,
) -> Dict[str, List[Dict[str, Any]]]:
    """Search YouTube Music and return ONLY songs and albums."""
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
        artist = _artist_name_from_item(it, "")
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
        artist = _artist_name_from_item(it, "")
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


def search_artists(query: str, *, artist_limit: int = 15) -> Dict[str, List[Dict[str, Any]]]:
    q = (query or "").strip()
    if not q:
        return {"artists": []}
    c = _client()
    raw = c.search(q, filter="artists", limit=int(artist_limit) if artist_limit else 15) or []
    artists: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for it in raw:
        if not isinstance(it, dict):
            continue
        browse_id = str(it.get("browseId") or "")
        if not browse_id or browse_id in seen:
            continue
        seen.add(browse_id)
        name = str(it.get("artist") or it.get("name") or "").strip()
        if not name:
            continue
        artists.append(
            {
                "kind": "artist",
                "browse_id": browse_id,
                "artist_id": browse_id,
                "name": name,
                "thumbnail_url": _best_thumb(it),
                "subscriber_count": str(it.get("subscribers") or "").strip(),
                "monthly_listeners": str(it.get("monthlyListeners") or it.get("monthly_listeners") or "").strip(),
                "ytmusic_url": f"https://music.youtube.com/browse/{browse_id}",
            }
        )
    return {"artists": artists}


def get_artist_overview(browse_id: str) -> Dict[str, Any]:
    bid = (browse_id or "").strip()
    if not bid:
        return {}
    c = _client()
    data = c.get_artist(bid) or {}
    name = str(data.get("name") or data.get("artist") or "").strip()
    songs = get_artist_popular_songs(bid, limit=10, _data=data)
    albums = get_artist_albums(bid, limit=12, _data=data)
    singles = get_artist_albums(bid, limit=12, category="singles", _data=data)
    return {
        "kind": "artist",
        "browse_id": bid,
        "artist_id": bid,
        "name": name,
        "description": str(data.get("description") or "").strip(),
        "thumbnail_url": _best_thumb(data),
        "subscriber_count": str(data.get("subscribers") or "").strip(),
        "views": str(data.get("views") or "").strip(),
        "songs_count": len(songs),
        "albums_count": len(albums),
        "singles_count": len(singles),
        "top_tracks_hint": [str((s or {}).get("title") or "") for s in songs[:5] if str((s or {}).get("title") or "")],
        "top_albums_hint": [str((a or {}).get("title") or "") for a in albums[:5] if str((a or {}).get("title") or "")],
        "ytmusic_url": f"https://music.youtube.com/browse/{bid}",
    }


def _parse_artist_song_rows(rows: List[Dict[str, Any]], fallback_artist: str) -> List[Dict[str, Any]]:
    songs: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for it in rows:
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        video_id = str(it.get("videoId") or "")
        artist = _artist_name_from_item(it, fallback_artist)
        album = ""
        alb = it.get("album")
        if isinstance(alb, dict):
            album = str(alb.get("name") or "").strip()
        elif isinstance(alb, str):
            album = alb.strip()
        key = (title.lower(), video_id)
        if key in seen:
            continue
        seen.add(key)
        songs.append(
            {
                "kind": "song",
                "video_id": video_id,
                "title": title,
                "artist": artist,
                "album": album,
                "duration_seconds": _duration_to_seconds(it.get("duration") or it.get("duration_seconds")),
                "thumbnail_url": _best_thumb(it),
                "youtube_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
                "ytmusic_url": f"https://music.youtube.com/watch?v={video_id}" if video_id else "",
            }
        )
    return songs


def get_artist_popular_songs(browse_id: str, *, limit: int = 10, _data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    bid = (browse_id or "").strip()
    if not bid:
        return []
    data = _data if isinstance(_data, dict) else (_client().get_artist(bid) or {})
    name = str(data.get("name") or data.get("artist") or "").strip()
    rows = _section_results(data.get("songs"))
    songs = _parse_artist_song_rows(rows, name)
    return songs[: max(0, int(limit))]


def _parse_artist_album_rows(rows: List[Dict[str, Any]], fallback_artist: str, category: str) -> List[Dict[str, Any]]:
    albums: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for it in rows:
        bid = str(it.get("browseId") or "").strip()
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        key = bid or title.lower()
        if key in seen:
            continue
        seen.add(key)
        artist = _artist_name_from_item(it, fallback_artist)
        albums.append(
            {
                "kind": "album",
                "browse_id": bid,
                "title": title,
                "artist": artist,
                "year": str(it.get("year") or "").strip(),
                "thumbnail_url": _best_thumb(it),
                "category": category,
                "ytmusic_url": f"https://music.youtube.com/browse/{bid}" if bid else "",
            }
        )
    return albums


def _expand_artist_album_section(bid: str, section: Any) -> List[Dict[str, Any]]:
    rows = _section_results(section)
    params = section.get("params") if isinstance(section, dict) else None
    if not params:
        return rows
    c = _client()
    get_more = getattr(c, "get_artist_albums", None)
    if not callable(get_more):
        return rows
    try:
        expanded = get_more(bid, params)
    except TypeError:
        try:
            expanded = get_more(params)
        except Exception:
            return rows
    except Exception:
        return rows
    more = _section_results(expanded)
    if more:
        return more
    return rows


def get_artist_albums(
    browse_id: str,
    *,
    limit: int = 50,
    category: str = "albums",
    _data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    bid = (browse_id or "").strip()
    if not bid:
        return []
    data = _data if isinstance(_data, dict) else (_client().get_artist(bid) or {})
    name = str(data.get("name") or data.get("artist") or "").strip()
    section_name = "albums" if category == "albums" else "singles"
    section = data.get(section_name) or {}
    rows = _expand_artist_album_section(bid, section)
    albums = _parse_artist_album_rows(rows, name, "album" if category == "albums" else section_name.rstrip("s"))
    return albums[: max(0, int(limit))]


def get_album_tracks(browse_id: str) -> List[Dict[str, Any]]:
    """Fetch an album tracklist from YouTube Music.

    Album payloads do not always expose the artist under a flat ``artist`` key.
    Some only provide an ``artists`` array at the album level, and many track rows
    omit per-track artists entirely. Resolve the album artist defensively and use it
    as a fallback for every track so `/api/album/{browse_id}` always returns usable
    artist metadata for the app.
    """
    bid = (browse_id or "").strip()
    if not bid:
        return []

    c = _client()
    data = c.get_album(bid) or {}
    tracks_raw = data.get("tracks") or []
    album_artist = _artist_name_from_item(data, "")

    tracks: List[Dict[str, Any]] = []
    for it in tracks_raw:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        artist = _artist_name_from_item(it, album_artist)
        tracks.append(
            {
                "title": title,
                "artist": artist,
                "duration_seconds": _duration_to_seconds(it.get("duration") or it.get("length")),
                "video_id": str(it.get("videoId") or ""),
            }
        )
    return tracks


def get_album_full(browse_id: str) -> Dict[str, Any]:
    bid = (browse_id or "").strip()
    if not bid:
        return {}
    c = _client()
    data = c.get_album(bid) or {}
    title = str(data.get("title") or "").strip()
    artist = _artist_name_from_item(data, "")
    year = str(data.get("year") or "").strip()
    thumb = _best_thumb(data)
    tracks = get_album_tracks(bid)
    return {
        "browse_id": bid,
        "title": title,
        "artist": artist,
        "year": year,
        "thumbnail_url": thumb,
        "tracks": tracks,
    }


# --- yt-dlp best-match helpers ---

_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _SANITIZE_RE.sub(" ", (s or "").lower()).strip()


def _token_set(s: str) -> set[str]:
    return {t for t in _norm(s).split() if t}


def _jaccard(a: str, b: str) -> float:
    aa, bb = _token_set(a), _token_set(b)
    if not aa or not bb:
        return 0.0
    inter = len(aa & bb)
    union = len(aa | bb)
    return inter / union if union else 0.0


@dataclass
class MatchResult:
    found: bool
    confidence: float = 0.0
    video_id: str = ""
    title: str = ""
    uploader: str = ""
    duration_seconds: Optional[int] = None

    @property
    def youtube_url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}" if self.video_id else ""

    @property
    def ytmusic_url(self) -> str:
        return f"https://music.youtube.com/watch?v={self.video_id}" if self.video_id else ""


def _ydl() -> YoutubeDL:
    return YoutubeDL(
        {
            "quiet": True,
            "skip_download": True,
            "noplaylist": True,
            "extract_flat": True,
            "default_search": "ytsearch5",
            "nocheckcertificate": True,
        }
    )


def _score_track_candidate(info: Dict[str, Any], *, title: str, artist: str, album: Optional[str], duration_seconds: Optional[int]) -> float:
    cand_title = str(info.get("title") or "")
    cand_uploader = str(info.get("uploader") or info.get("channel") or "")
    cand_duration = info.get("duration")

    score = 0.0
    score += 0.60 * _jaccard(title, cand_title)
    score += 0.30 * _jaccard(artist, cand_uploader)

    if album:
        desc = str(info.get("description") or "")
        score += 0.05 * max(_jaccard(album, cand_title), _jaccard(album, desc))

    if duration_seconds and isinstance(cand_duration, (int, float)):
        diff = abs(int(cand_duration) - int(duration_seconds))
        if diff <= 2:
            score += 0.10
        elif diff <= 5:
            score += 0.05
        elif diff > 20:
            score -= 0.10

    if "official" in _norm(cand_title) or "official" in _norm(cand_uploader):
        score += 0.03
    if "topic" in _norm(cand_uploader):
        score += 0.02

    return max(0.0, min(1.0, score))


def _score_album_candidate(info: Dict[str, Any], *, album_title: str, artist: str) -> float:
    cand_title = str(info.get("title") or "")
    cand_uploader = str(info.get("uploader") or info.get("channel") or "")
    score = 0.0
    score += 0.70 * _jaccard(album_title, cand_title)
    score += 0.25 * _jaccard(artist, cand_uploader)
    if "album" in _norm(cand_title):
        score += 0.05
    return max(0.0, min(1.0, score))



def find_song(*, title: str, artist: str, album: Optional[str] = None, duration_seconds: Optional[int] = None) -> MatchResult:
    """Backward-compatible alias used by stations_engine and older callers."""
    return find_track(title=title, artist=artist, album=album, duration_seconds=duration_seconds)

def find_track(*, title: str, artist: str, album: Optional[str] = None, duration_seconds: Optional[int] = None) -> MatchResult:
    query = f"{title} {artist}"
    ydl = _ydl()
    data = ydl.extract_info(f"ytsearch5:{query}", download=False)
    entries = (data or {}).get("entries") or []

    best: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)
    for e in entries:
        if not isinstance(e, dict):
            continue
        score = _score_track_candidate(e, title=title, artist=artist, album=album, duration_seconds=duration_seconds)
        if score > best[0]:
            best = (score, e)

    if best[1] is None or best[0] < 0.45:
        return MatchResult(found=False)

    e = best[1]
    return MatchResult(
        found=True,
        confidence=float(best[0]),
        video_id=str(e.get("id") or ""),
        title=str(e.get("title") or ""),
        uploader=str(e.get("uploader") or e.get("channel") or ""),
        duration_seconds=int(e.get("duration")) if e.get("duration") is not None else None,
    )


def find_album(*, album_title: str, artist: str) -> MatchResult:
    query = f"{album_title} {artist} album"
    ydl = _ydl()
    data = ydl.extract_info(f"ytsearch5:{query}", download=False)
    entries = (data or {}).get("entries") or []

    best: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)
    for e in entries:
        if not isinstance(e, dict):
            continue
        score = _score_album_candidate(e, album_title=album_title, artist=artist)
        if score > best[0]:
            best = (score, e)

    if best[1] is None or best[0] < 0.45:
        return MatchResult(found=False)

    e = best[1]
    return MatchResult(
        found=True,
        confidence=float(best[0]),
        video_id=str(e.get("id") or ""),
        title=str(e.get("title") or ""),
        uploader=str(e.get("uploader") or e.get("channel") or ""),
        duration_seconds=int(e.get("duration")) if e.get("duration") is not None else None,
    )
