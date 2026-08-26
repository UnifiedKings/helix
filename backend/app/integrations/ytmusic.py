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


def _album_playlist_entries(playlist_id: str) -> List[Dict[str, Any]]:
    """Return flat yt-dlp entries for a YTMusic album audio playlist.

    ytmusicapi's album/playlist objects sometimes omit videoId for some rows.
    yt-dlp's flat playlist extraction is a useful second source for every track id.
    """
    pid = (playlist_id or "").strip()
    if not pid:
        return []
    url = f"https://music.youtube.com/playlist?list={pid}"
    try:
        ydl = YoutubeDL(
            {
                "quiet": True,
                "skip_download": True,
                "extract_flat": True,
                "ignoreerrors": True,
                "nocheckcertificate": True,
            }
        )
        data = ydl.extract_info(url, download=False) or {}
        entries = data.get("entries") or []
        return [e for e in entries if isinstance(e, dict)]
    except Exception:
        return []


def _video_id_from_flat_entry(entry: Dict[str, Any]) -> str:
    vid = str(entry.get("id") or entry.get("video_id") or "").strip()
    if vid:
        return vid
    url = str(entry.get("url") or entry.get("webpage_url") or "").strip()
    m = re.search(r"(?:v=|/watch/|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else ""


def _best_thumb(item: Dict[str, Any]) -> str:
    """Choose artwork-shaped thumbnails before simply choosing raw width.

    YouTube Music frequently exposes both proper square album artwork and
    wider/padded video thumbnails.  Album/library artwork should prefer the
    square asset even when a widescreen thumbnail has a larger width.
    """
    thumbs = item.get("thumbnails") or []
    if not isinstance(thumbs, list) or not thumbs:
        return ""

    def _score(raw: Any) -> tuple[int, int, int]:
        thumb = raw if isinstance(raw, dict) else {}
        try:
            width = int(thumb.get("width") or 0)
        except (TypeError, ValueError):
            width = 0
        try:
            height = int(thumb.get("height") or 0)
        except (TypeError, ValueError):
            height = 0

        if width > 0 and height > 0:
            square_error = abs(width - height) / max(width, height)
            if square_error <= 0.08:
                shape_rank = 2
            elif square_error <= 0.20:
                shape_rank = 1
            else:
                shape_rank = 0
            return (shape_rank, min(width, height), width * height)

        # Unknown dimensions are a last resort, but retain width as a weak
        # quality signal when ytmusicapi does provide only one dimension.
        return (-1, width, 0)

    best = max(thumbs, key=_score)
    return str((best or {}).get("url") or "") if isinstance(best, dict) else ""


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

    # A zero limit explicitly disables that result type. Several station paths use
    # song_limit=0 or album_limit=0 to avoid an unnecessary second network search.
    song_limit = max(0, int(song_limit or 0))
    album_limit = max(0, int(album_limit or 0))
    songs_raw = (c.search(q, filter="songs", limit=song_limit) or []) if song_limit > 0 else []
    albums_raw = (c.search(q, filter="albums", limit=album_limit) or []) if album_limit > 0 else []

    songs: List[Dict[str, Any]] = []
    for it in songs_raw:
        if not isinstance(it, dict):
            continue
        vid = str(it.get("videoId") or "")
        if not vid:
            continue
        artist = _artist_name_from_item(it, "")
        album = ""
        album_browse_id = ""
        alb = it.get("album")
        if isinstance(alb, dict):
            album = str(alb.get("name") or "")
            # ytmusicapi currently exposes the album browse id as `id` on song
            # search results, but accept browseId/browse_id too for compatibility.
            album_browse_id = str(
                alb.get("id")
                or alb.get("browseId")
                or alb.get("browse_id")
                or ""
            ).strip()
        songs.append(
            {
                "kind": "song",
                "video_id": vid,
                "title": str(it.get("title") or ""),
                "artist": artist,
                "album": album,
                "album_browse_id": album_browse_id,
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


def _norm_artist_lookup(value: str) -> str:
    value = (value or "").strip().lower()
    value = value.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"^the\s+", "", value)
    value = re.sub(r",\s*the$", "", value)
    value = re.sub(r"[^a-z0-9\s]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def find_artist_by_name(name: str, *, artist_limit: int = 8) -> Dict[str, Any]:
    """Resolve an artist name to a YouTube Music artist browse id.

    Prefer an exact normalized artist-name match.  If YouTube Music does not
    return one, use the first artist search result rather than introducing a
    second identity service just to resolve the artist.
    """
    query = (name or "").strip()
    if not query:
        return {}
    rows = (search_artists(query, artist_limit=max(1, int(artist_limit))) or {}).get("artists") or []
    wanted = _norm_artist_lookup(query)
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _norm_artist_lookup(str(row.get("name") or "")) == wanted:
            return row
    for row in rows:
        if isinstance(row, dict) and str(row.get("browse_id") or "").strip():
            return row
    return {}


def get_artist_related_artists(
    browse_id: str,
    *,
    limit: int = 100,
    _data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return YouTube Music's related artists ("Fans might also like")."""
    bid = (browse_id or "").strip()
    if not bid:
        return []
    data = _data if isinstance(_data, dict) else (_client().get_artist(bid) or {})
    rows = _section_results(data.get("related"))
    related: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for rank, item in enumerate(rows, start=1):
        name = str(item.get("artist") or item.get("name") or item.get("title") or "").strip()
        related_id = str(item.get("browseId") or item.get("browse_id") or item.get("artist_id") or "").strip()
        key = related_id or _norm_artist_lookup(name)
        if not name or not key or key in seen:
            continue
        seen.add(key)
        related.append(
            {
                "name": name,
                "artist": name,
                "browse_id": related_id,
                "artist_id": related_id,
                "rank": rank,
                "thumbnail_url": _best_thumb(item),
                "ytmusic_url": f"https://music.youtube.com/browse/{related_id}" if related_id else "",
            }
        )
        if len(related) >= max(0, int(limit)):
            break
    return related


def _expanded_artist_song_rows(client: YTMusic, songs_section: Any, limit: int) -> List[Dict[str, Any]]:
    rows = _section_results(songs_section)
    if not isinstance(songs_section, dict) or len(rows) >= limit:
        return rows
    browse_id = str(songs_section.get("browseId") or songs_section.get("browse_id") or "").strip()
    if not browse_id:
        return rows

    # get_artist() exposes the songs continuation as a browseId. ytmusicapi's
    # documented path is to pass it to get_playlist(); older versions can be
    # inconsistent about whether the leading VL is accepted, so support both.
    candidates = [browse_id]
    if browse_id.startswith("VL") and len(browse_id) > 2:
        candidates.append(browse_id[2:])
    for playlist_id in candidates:
        try:
            payload = client.get_playlist(playlist_id, limit=max(1, int(limit))) or {}
        except Exception:
            continue
        playlist_rows = payload.get("tracks") or []
        if isinstance(playlist_rows, list) and playlist_rows:
            return [row for row in playlist_rows if isinstance(row, dict)]
    return rows


def get_artist_popular_songs(browse_id: str, *, limit: int = 10, _data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    bid = (browse_id or "").strip()
    if not bid:
        return []
    wanted = max(0, int(limit))
    if wanted <= 0:
        return []
    client = _client()
    data = _data if isinstance(_data, dict) else (client.get_artist(bid) or {})
    name = str(data.get("name") or data.get("artist") or "").strip()
    rows = _expanded_artist_song_rows(client, data.get("songs"), wanted)
    songs = _parse_artist_song_rows(rows, name)
    return songs[:wanted]


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

    def norm_title(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()

    # ytmusicapi album rows can sometimes omit videoId for alternating rows or
    # partial rows. The album payload usually also has an audioPlaylistId; the
    # playlist payload tends to contain video IDs for every track. Build lookup
    # maps from that playlist and merge them into the album track list.
    playlist_rows: List[Dict[str, Any]] = []
    playlist_id = str(
        data.get("audioPlaylistId")
        or data.get("playlistId")
        or data.get("audioPlaylist")
        or ""
    ).strip()
    if playlist_id:
        try:
            playlist_payload = c.get_playlist(playlist_id, limit=500) or {}
            raw_playlist_tracks = playlist_payload.get("tracks") or []
            if isinstance(raw_playlist_tracks, list):
                playlist_rows = [row for row in raw_playlist_tracks if isinstance(row, dict)]
        except Exception:
            playlist_rows = []

    # Third source: yt-dlp flat playlist entries. This often has video IDs for
    # rows that ytmusicapi returns without playable ids.
    flat_rows: List[Dict[str, Any]] = _album_playlist_entries(playlist_id)
    flat_by_title: Dict[str, Dict[str, Any]] = {}
    for row in flat_rows:
        key = norm_title(str(row.get("title") or ""))
        if key and key not in flat_by_title:
            flat_by_title[key] = row

    playlist_by_title: Dict[str, Dict[str, Any]] = {}
    for row in playlist_rows:
        key = norm_title(str(row.get("title") or ""))
        if key and key not in playlist_by_title:
            playlist_by_title[key] = row

    tracks: List[Dict[str, Any]] = []
    for index, it in enumerate(tracks_raw, start=1):
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "").strip()
        if not title:
            continue

        artist = _artist_name_from_item(it, album_artist)
        duration_seconds = _duration_to_seconds(it.get("duration") or it.get("length"))
        video_id = str(it.get("videoId") or it.get("video_id") or "").strip()

        title_key = norm_title(title)
        playlist_match = playlist_by_title.get(title_key)
        flat_match = flat_by_title.get(title_key)

        if not playlist_match and 0 <= index - 1 < len(playlist_rows):
            # Fallback by album order. This is intentionally only used when the
            # title lookup failed; it fixes partial album rows without making
            # normal title-based matching less accurate.
            playlist_match = playlist_rows[index - 1]
        if not flat_match and 0 <= index - 1 < len(flat_rows):
            flat_match = flat_rows[index - 1]

        if playlist_match:
            if not video_id:
                video_id = str(playlist_match.get("videoId") or playlist_match.get("video_id") or "").strip()
            if not duration_seconds:
                duration_seconds = _duration_to_seconds(playlist_match.get("duration") or playlist_match.get("length"))
            if (not artist or artist == album_artist) and playlist_match:
                artist = _artist_name_from_item(playlist_match, album_artist)

        if flat_match:
            if not video_id:
                video_id = _video_id_from_flat_entry(flat_match)
            if not duration_seconds:
                duration_seconds = _duration_to_seconds(flat_match.get("duration") or flat_match.get("length"))

        tracks.append(
            {
                "title": title,
                "artist": artist,
                "duration_seconds": duration_seconds,
                "duration_ms": int(duration_seconds * 1000) if duration_seconds else 0,
                "video_id": video_id,
                "videoId": video_id,
                "track_no": index,
                "pos": index,
            }
        )

    # If get_album returned an incomplete/odd-only track list but the playlist is
    # complete, append playlist-only rows that are not already present by title.
    seen_titles = {norm_title(str(row.get("title") or "")) for row in tracks}

    def append_playlist_only(row: Dict[str, Any], *, flat: bool = False) -> None:
        title = str(row.get("title") or "").strip()
        key = norm_title(title)
        if not title or key in seen_titles:
            return
        index = len(tracks) + 1
        duration_seconds = _duration_to_seconds(row.get("duration") or row.get("length"))
        video_id = _video_id_from_flat_entry(row) if flat else str(row.get("videoId") or row.get("video_id") or "").strip()
        tracks.append(
            {
                "title": title,
                "artist": _artist_name_from_item(row, album_artist),
                "duration_seconds": duration_seconds,
                "duration_ms": int(duration_seconds * 1000) if duration_seconds else 0,
                "video_id": video_id,
                "videoId": video_id,
                "track_no": index,
                "pos": index,
            }
        )
        seen_titles.add(key)

    for row in playlist_rows:
        append_playlist_only(row, flat=False)
    for row in flat_rows:
        append_playlist_only(row, flat=True)

    return tracks


def _is_video_thumbnail_url(url: str) -> bool:
    value = str(url or "").lower()
    return "i.ytimg.com/vi/" in value or "img.youtube.com/vi/" in value


def _album_search_thumbnail(client: YTMusic, browse_id: str, title: str, artist: str) -> str:
    """Resolve the album artwork shown by YouTube Music search.

    ``get_album()`` can occasionally expose a YouTube video thumbnail even
    though YouTube Music itself has a proper square ``yt3.googleusercontent``
    album image. Album search results are a reliable place to recover that
    artwork. Prefer an exact browse-id match and only use title/artist matching
    as a conservative fallback.
    """
    query = " ".join(part for part in (artist.strip(), title.strip()) if part).strip()
    if not query:
        return ""

    try:
        rows = client.search(query, filter="albums", limit=8) or []
    except Exception:
        return ""

    def norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

    wanted_title = norm(title)
    wanted_artist = norm(artist)
    fallback = ""

    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = _best_thumb(row)
        if not candidate:
            continue

        row_id = str(row.get("browseId") or row.get("browse_id") or "").strip()
        if row_id and row_id == browse_id:
            return candidate

        row_title = norm(str(row.get("title") or ""))
        row_artist = norm(_artist_name_from_item(row, ""))
        if wanted_title and row_title == wanted_title:
            if not wanted_artist or not row_artist or row_artist == wanted_artist:
                fallback = fallback or candidate

    return fallback





def _watch_playlist_thumbnail(client: YTMusic, tracks: list[Dict[str, Any]]) -> str:
    """Resolve artwork from YTMusic's watch-playlist response for an album track.

    This is closer to the actual music-player surface than the generic album
    payload, and often exposes the same yt3.googleusercontent.com artwork that
    Helix receives from normal YTMusic song search.
    """
    if not tracks:
        return ""
    first = tracks[0] if isinstance(tracks[0], dict) else {}
    video_id = str(first.get("video_id") or first.get("videoId") or "").strip()
    if not video_id:
        return ""
    try:
        payload = client.get_watch_playlist(videoId=video_id, limit=5) or {}
    except Exception:
        return ""
    rows = payload.get("tracks") or []
    if not isinstance(rows, list):
        return ""
    fallback = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = _best_thumb(row)
        if not candidate:
            continue
        row_vid = str(row.get("videoId") or row.get("video_id") or "").strip()
        if row_vid == video_id:
            return candidate
        if not fallback:
            fallback = candidate
    return fallback

def _track_search_thumbnail(client: YTMusic, tracks: list[Dict[str, Any]], album_title: str, album_artist: str) -> str:
    """Recover the artwork attached to an exact YTMusic song search result.

    Search playback already receives these yt3.googleusercontent.com images.
    Album expansion sometimes receives an i.ytimg.com video frame instead, so
    use the first album track as a second authoritative artwork source.
    """
    if not tracks:
        return ""
    first = tracks[0] if isinstance(tracks[0], dict) else {}
    title = str(first.get("title") or "").strip()
    video_id = str(first.get("video_id") or first.get("videoId") or "").strip()
    query = " ".join(part for part in (album_artist.strip(), title) if part).strip()
    if not query:
        return ""
    try:
        rows = client.search(query, filter="songs", limit=8) or []
    except Exception:
        return ""

    def norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()

    want_title = norm(title)
    want_artist = norm(album_artist)
    fallback = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = _best_thumb(row)
        if not candidate:
            continue
        row_vid = str(row.get("videoId") or row.get("video_id") or "").strip()
        if video_id and row_vid == video_id:
            return candidate
        row_title = norm(str(row.get("title") or ""))
        row_artist = norm(_artist_name_from_item(row, ""))
        if want_title and row_title == want_title and (not want_artist or not row_artist or row_artist == want_artist):
            fallback = fallback or candidate
    return fallback


def _upgrade_yt3_square_url(url: str, size: int = 544) -> str:
    """Ask Google's artwork CDN for a larger square variant when possible."""
    value = str(url or "").strip()
    if "yt3.googleusercontent.com" not in value.lower():
        return value
    # Preserve the base asset token; only replace the image transform suffix.
    if "=" in value:
        value = value.split("=", 1)[0]
    return f"{value}=w{size}-h{size}-l90-rj"

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

    # Prefer the artwork attached to the album itself. Song/watch thumbnails
    # can be square wrappers around a padded 16:9 video frame.
    watch_thumb = _watch_playlist_thumbnail(c, tracks)
    search_thumb = _album_search_thumbnail(c, bid, title, artist)
    track_thumb = _track_search_thumbnail(c, tracks, title, artist)
    direct_album_thumb = thumb if thumb and not _is_video_thumbnail_url(thumb) else ""
    recovery_candidates = [search_thumb, track_thumb, watch_thumb]
    recovery_clean = next(
        (u for u in recovery_candidates if u and not _is_video_thumbnail_url(u)),
        "",
    )
    thumb = direct_album_thumb or recovery_clean or thumb or next(
        (u for u in recovery_candidates if u),
        "",
    )
    thumb = _upgrade_yt3_square_url(thumb)
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




def get_song_radio(video_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
    """Return YouTube Music radio candidates for one seed song.

    ``get_watch_playlist(..., radio=True)`` is the supported ytmusicapi path for
    the queue YouTube Music builds around a song. Helix pins a ytmusicapi
    release that understands the current WEB_REMIX response shape. The
    deterministic RDAMVM radio playlist remains a fallback because YouTube can
    occasionally expose one form but not the other for a particular seed.
    """
    vid = (video_id or "").strip()
    if not vid:
        return []

    c = _client()
    requested = max(1, int(limit))
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    try:
        data = c.get_watch_playlist(videoId=vid, limit=requested, radio=True) or {}
        raw_rows = data.get("tracks") or []
        if isinstance(raw_rows, list):
            rows = [it for it in raw_rows if isinstance(it, dict)]
    except Exception as exc:
        errors.append(f"get_watch_playlist: {exc}")

    if not rows:
        try:
            data = c.get_playlist(f"RDAMVM{vid}", limit=requested) or {}
            raw_rows = data.get("tracks") or []
            if isinstance(raw_rows, list):
                rows = [it for it in raw_rows if isinstance(it, dict)]
        except Exception as exc:
            errors.append(f"get_playlist: {exc}")

    if not rows and errors:
        raise RuntimeError("; ".join(errors))

    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for it in rows:
        item_vid = str(it.get("videoId") or "").strip()
        if not item_vid or item_vid in seen:
            continue
        seen.add(item_vid)
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        artist = _artist_name_from_item(it, "")
        album = ""
        alb = it.get("album")
        if isinstance(alb, dict):
            album = str(alb.get("name") or alb.get("title") or "").strip()
        elif isinstance(alb, str):
            album = alb.strip()
        duration_seconds = _duration_to_seconds(it.get("length") or it.get("duration"))
        out.append({
            "kind": "song",
            "video_id": item_vid,
            "title": title,
            "artist": artist,
            "album": album,
            "duration_seconds": duration_seconds,
            "duration_ms": int(duration_seconds or 0) * 1000,
            "thumbnail_url": _best_thumb(it),
            "youtube_url": f"https://www.youtube.com/watch?v={item_vid}",
            "ytmusic_url": f"https://music.youtube.com/watch?v={item_vid}",
        })
        if len(out) >= requested:
            break
    return out

def find_song(*, title: str, artist: str, album: Optional[str] = None, duration_seconds: Optional[int] = None) -> MatchResult:
    """Backward-compatible alias used by stations_engine and older callers."""
    return find_track(title=title, artist=artist, album=album, duration_seconds=duration_seconds)

def find_track(*, title: str, artist: str, album: Optional[str] = None, duration_seconds: Optional[int] = None) -> MatchResult:
    queries = [f"{title} {artist}"]
    if album:
        queries.append(f"{title} {artist} {album}")
        queries.append(f"{artist} {album} {title}")

    ydl = _ydl()
    best: Tuple[float, Optional[Dict[str, Any]]] = (0.0, None)
    seen: set[str] = set()

    for query in queries:
        try:
            data = ydl.extract_info(f"ytsearch10:{query}", download=False)
        except Exception:
            continue
        entries = (data or {}).get("entries") or []
        for e in entries:
            if not isinstance(e, dict):
                continue
            vid = str(e.get("id") or e.get("video_id") or "")
            if vid and vid in seen:
                continue
            if vid:
                seen.add(vid)
            score = _score_track_candidate(e, title=title, artist=artist, album=album, duration_seconds=duration_seconds)
            if score > best[0]:
                best = (score, e)

    if best[1] is None:
        return MatchResult(found=False)

    cand_title = str(best[1].get("title") or "")
    strong_title_match = _jaccard(title, cand_title) >= 0.60 or _norm(title) in _norm(cand_title)
    threshold = 0.36 if strong_title_match else 0.45
    if best[0] < threshold:
        return MatchResult(found=False)

    e = best[1]
    return MatchResult(
        found=True,
        confidence=float(best[0]),
        video_id=str(e.get("id") or e.get("video_id") or ""),
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
