from __future__ import annotations

import csv
import html as html_lib
import io
import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from .art_sources import yt_thumbnail_url


@dataclass
class ImportedTrack:
    source: str
    source_track_id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    artwork_url: str = ""
    isrc: str = ""
    yt_video_id: str = ""
    raw: Optional[Dict[str, Any]] = None


def _norm(value: str) -> str:
    value = html_lib.unescape(value or "").casefold()
    value = re.sub(r"\([^)]*(?:official|lyrics?|audio|video|visualizer|live|remaster|sped up|slowed)[^)]*\)", " ", value)
    value = re.sub(r"\[[^]]*(?:official|lyrics?|audio|video|visualizer|live|remaster|sped up|slowed)[^]]*\]", " ", value)
    value = re.sub(r"\b(?:official\s+)?(?:music\s+)?video\b|\blyrics?\b|\bofficial audio\b", " ", value)
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def _similar(a: str, b: str) -> float:
    aa, bb = _norm(a), _norm(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    return SequenceMatcher(None, aa, bb).ratio()


def _duration_score(imported_ms: int, candidate_seconds: Any) -> float:
    if not imported_ms or not candidate_seconds:
        return 0.5
    try:
        candidate_ms = int(candidate_seconds) * 1000
    except (TypeError, ValueError):
        return 0.5
    delta = abs(imported_ms - candidate_ms)
    if delta <= 2500:
        return 1.0
    if delta <= 6000:
        return 0.9
    if delta <= 12000:
        return 0.65
    if delta <= 25000:
        return 0.35
    return 0.0


def _candidate_score(track: ImportedTrack, candidate: Dict[str, Any]) -> float:
    title = _similar(track.title, str(candidate.get("title") or ""))
    artist = _similar(track.artist, str(candidate.get("artist") or ""))
    album = _similar(track.album, str(candidate.get("album") or "")) if track.album and candidate.get("album") else 0.5
    duration = _duration_score(track.duration_ms, candidate.get("duration_seconds"))
    return (title * 0.46) + (artist * 0.34) + (album * 0.10) + (duration * 0.10)


def _yt_candidate_payload(candidate: Dict[str, Any]) -> Dict[str, Any]:
    vid = str(candidate.get("video_id") or candidate.get("videoId") or "").strip()
    return {
        "title": str(candidate.get("title") or "").strip(),
        "artist": str(candidate.get("artist") or "").strip(),
        "album": str(candidate.get("album") or "").strip(),
        "duration_ms": int(candidate.get("duration_seconds") or 0) * 1000,
        "art_url": str(candidate.get("thumbnail_url") or "").strip() or (yt_thumbnail_url(vid) if vid else ""),
        "source": "ytmusic",
        "subsonic_song_id": "",
        "yt_video_id": vid,
        "yt_browse_id": str(candidate.get("album_browse_id") or "").strip(),
    }


def parse_exportify_csv(content: str) -> List[ImportedTrack]:
    reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
    fields = set(reader.fieldnames or [])
    required = {"Track URI", "Track Name", "Artist Name(s)"}
    if not required.issubset(fields):
        raise ValueError("This does not look like an Exportify CSV export.")
    tracks: List[ImportedTrack] = []
    for row in reader:
        title = (row.get("Track Name") or "").strip()
        artist = (row.get("Artist Name(s)") or "").strip()
        if not title or not artist:
            continue
        uri = (row.get("Track URI") or "").strip()
        source_id = uri.rsplit(":", 1)[-1] if uri else ""
        try:
            duration_ms = int(float(row.get("Duration (ms)") or 0))
        except (TypeError, ValueError):
            duration_ms = 0
        tracks.append(ImportedTrack(
            source="spotify",
            source_track_id=source_id,
            title=title,
            artist=artist,
            album=(row.get("Album Name") or "").strip(),
            duration_ms=duration_ms,
            raw=dict(row),
        ))
    return tracks


def parse_helix_json(content: str) -> tuple[str, List[ImportedTrack]]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid Helix JSON export.") from exc
    if not isinstance(payload, dict) or payload.get("format") != "helix-playlist":
        raise ValueError("This does not look like a Helix playlist export.")
    playlist = payload.get("playlist") or {}
    name = str(playlist.get("name") or "Helix playlist")
    tracks: List[ImportedTrack] = []
    for raw in playlist.get("tracks") or []:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        artist = str(raw.get("artist") or "").strip()
        if not title or not artist:
            continue
        tracks.append(ImportedTrack(
            source="helix",
            source_track_id=str(raw.get("source_id") or raw.get("yt_video_id") or raw.get("subsonic_song_id") or ""),
            title=title,
            artist=artist,
            album=str(raw.get("album") or ""),
            duration_ms=int(raw.get("duration_ms") or 0),
            artwork_url=str(raw.get("art_url") or ""),
            yt_video_id=str(raw.get("yt_video_id") or ""),
            raw=raw,
        ))
    return name, tracks


def parse_ytmusic_saved_html(content: str) -> tuple[Optional[int], List[ImportedTrack]]:
    stripped = (content or "").lstrip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("format") == "helix-ytmusic-saved-page":
            reported_raw = payload.get("reported_count")
            try:
                reported = int(reported_raw) if reported_raw is not None else None
            except (TypeError, ValueError):
                reported = None
            tracks: List[ImportedTrack] = []
            seen: set[str] = set()
            for raw in payload.get("tracks") or []:
                if not isinstance(raw, dict):
                    continue
                vid = str(raw.get("yt_video_id") or raw.get("video_id") or "").strip()
                title = str(raw.get("title") or "").strip()
                artist = str(raw.get("artist") or "").strip()
                if not vid or not title or vid in seen:
                    continue
                seen.add(vid)
                tracks.append(ImportedTrack(
                    source="ytmusic", source_track_id=vid, title=title, artist=artist,
                    album=str(raw.get("album") or "").strip(), duration_ms=int(raw.get("duration_ms") or 0),
                    artwork_url=yt_thumbnail_url(vid), yt_video_id=vid, raw=raw,
                ))
            return reported, tracks

    count_match = re.search(r">\s*([\d,]+)\s+songs\s*<", content, re.I)
    reported = int(count_match.group(1).replace(",", "")) if count_match else None

    shelf_match = re.search(r'<ytmusic-playlist-shelf-renderer\b.*?</ytmusic-playlist-shelf-renderer>', content, re.I | re.S)
    scope = shelf_match.group(0) if shelf_match else content
    row_pattern = re.compile(r'<ytmusic-responsive-list-item-renderer\b(?P<attrs>[^>]*)>(?P<body>.*?)</ytmusic-responsive-list-item-renderer>', re.I | re.S)
    anchor_pattern = re.compile(r'<a\b[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<body>.*?)</a>', re.I | re.S)
    tag_pattern = re.compile(r'<[^>]+>')
    tracks: List[ImportedTrack] = []
    seen: set[str] = set()

    for row in row_pattern.finditer(scope):
        body = row.group("body")
        links = []
        for anchor in anchor_pattern.finditer(body):
            href = html_lib.unescape(anchor.group("href"))
            text = html_lib.unescape(tag_pattern.sub("", anchor.group("body"))).strip()
            if text:
                links.append((href, " ".join(text.split())))
        watch = next(((href, text) for href, text in links if "/watch?" in href and "v=" in href), None)
        if not watch:
            continue
        parsed = urlparse(watch[0])
        vid = parse_qs(parsed.query).get("v", [""])[0]
        if not vid or vid in seen:
            continue
        seen.add(vid)
        title = watch[1]
        artist = ""
        album = ""
        for href, text in links:
            if "/channel/" in href or "/browse/UC" in href:
                artist = text
                break
        for href, text in links:
            if "/browse/" in href and text != artist:
                album = text
                break

        text_content = html_lib.unescape(tag_pattern.sub(" ", body))
        durations = re.findall(r"\b(?:(\d+):)?(\d{1,2}):(\d{2})\b", text_content)
        duration_ms = 0
        if durations:
            h, m, s = durations[-1]
            duration_ms = ((int(h or 0) * 3600) + int(m) * 60 + int(s)) * 1000
        tracks.append(ImportedTrack(
            source="ytmusic",
            source_track_id=vid,
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
            artwork_url=yt_thumbnail_url(vid),
            yt_video_id=vid,
        ))
    return reported, tracks


def _playlist_id_from_youtube_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    host = parsed.hostname or ""
    if host not in {"music.youtube.com", "www.youtube.com", "youtube.com", "m.youtube.com"}:
        return ""
    return parse_qs(parsed.query).get("list", [""])[0].strip()


def parse_ytmusic_playlist_url(url: str) -> tuple[str, List[ImportedTrack]]:
    from .integrations.ytmusic import _client as _ytmusic_client, _best_thumb, _duration_to_seconds, _artist_name_from_item
    playlist_id = _playlist_id_from_youtube_url(url)
    if not playlist_id:
        raise ValueError("Paste a valid YouTube Music playlist share URL.")
    if playlist_id in {"LM", "VLLM"}:
        raise ValueError("Liked Music cannot be imported from its share URL. Save the Liked Music page and upload the HTML file instead.")
    payload = _ytmusic_client().get_playlist(playlist_id, limit=None) or {}
    tracks: List[ImportedTrack] = []
    for raw in payload.get("tracks") or []:
        if not isinstance(raw, dict):
            continue
        vid = str(raw.get("videoId") or "").strip()
        title = str(raw.get("title") or "").strip()
        artist = _artist_name_from_item(raw, "")
        if not title or not artist:
            continue
        album_obj = raw.get("album") or {}
        album = str(album_obj.get("name") or "") if isinstance(album_obj, dict) else ""
        tracks.append(ImportedTrack(
            source="ytmusic",
            source_track_id=vid,
            title=title,
            artist=artist,
            album=album,
            duration_ms=int(_duration_to_seconds(raw.get("duration")) or raw.get("duration_seconds") or 0) * 1000,
            artwork_url=_best_thumb(raw) or (yt_thumbnail_url(vid) if vid else ""),
            yt_video_id=vid,
            raw=raw,
        ))
    return str(payload.get("title") or "YouTube Music playlist"), tracks


def _pandora_playlist_id(url: str) -> str:
    match = re.search(r"/playlist/(PL:[^/?#]+)", url or "")
    return match.group(1) if match else ""


async def parse_pandora_playlist_url(url: str) -> tuple[str, Optional[int], List[ImportedTrack]]:
    pandora_id = _pandora_playlist_id(url)
    if not pandora_id:
        raise ValueError("Paste a valid Pandora playlist share URL.")

    # Pandora's public web player uses a two-stage anonymous session. The first
    # token is the web client's bootstrap token; anonymousLogin then returns the
    # short-lived anonymous auth token used by playlist endpoints. Neither step
    # requires a Pandora account for public playlists.
    PANDORA_WEB_BOOTSTRAP_AUTH = "BXhnXsnuZ/dylblMN82eGhwPwHxbT0dwNmKEG0zTc+sF8rjy9WzVmqTQ=="
    anonymous_login_endpoint = "https://www.pandora.com/api/v1/auth/anonymousLogin"
    tracks_endpoint = "https://www.pandora.com/api/v7/playlists/getTracks"

    share_url = (url or "").strip()
    limit = 20
    offset = 0
    playlist_version = 0
    tracks: List[ImportedTrack] = []
    total: Optional[int] = None
    name = "Pandora playlist"
    seen: set[str] = set()

    def _pandora_art_url(value: Any) -> str:
        if isinstance(value, dict):
            value = value.get("artUrl") or value.get("url") or value.get("thorId") or ""
        art = str(value or "").strip()
        if not art:
            return ""
        if art.startswith("//"):
            return "https:" + art
        if art.startswith("http://") or art.startswith("https://"):
            return art
        # Pandora annotations normally return paths such as
        # images/.../_500W_500H.jpg. The web client serves these from p-cdn.
        return "https://content-images.p-cdn.com/" + art.lstrip("/")

    def consume_payload(payload: Dict[str, Any]) -> int:
        nonlocal total, name, playlist_version

        name = str(payload.get("name") or payload.get("playlistName") or name)
        try:
            total_value = payload.get("totalTracks")
            if total_value is None:
                total_value = payload.get("totalCount")
            if total_value is not None:
                total = int(total_value)
        except (TypeError, ValueError):
            pass

        try:
            returned_version = payload.get("version")
            if returned_version is not None:
                playlist_version = int(returned_version)
        except (TypeError, ValueError):
            pass

        annotations = payload.get("annotations") or {}
        items = payload.get("tracks") or payload.get("items") or []
        if not isinstance(items, list):
            return 0

        for item in items:
            if not isinstance(item, dict):
                continue
            track_id = str(item.get("trackPandoraId") or item.get("pandoraId") or item.get("itemId") or "")
            ann = annotations.get(track_id) if isinstance(annotations, dict) else None
            if not isinstance(ann, dict):
                ann = item

            title = str(ann.get("name") or ann.get("title") or ann.get("trackName") or "").strip()
            artist = str(ann.get("artistName") or ann.get("artist") or "").strip()
            if not title or not artist:
                continue

            dedupe = track_id or f"{_norm(title)}|{_norm(artist)}"
            if dedupe in seen:
                continue
            seen.add(dedupe)

            album = str(ann.get("albumName") or ann.get("album") or "").strip()
            duration_ms = 0
            try:
                if ann.get("durationMillis") is not None:
                    duration_ms = int(ann.get("durationMillis") or 0)
                else:
                    duration = ann.get("duration") or item.get("duration") or 0
                    duration_ms = int(float(duration) * 1000)
            except (TypeError, ValueError):
                duration_ms = 0

            art = _pandora_art_url(ann.get("icon") or ann.get("artUrl") or ann.get("albumArtUrl"))
            tracks.append(ImportedTrack(
                source="pandora",
                source_track_id=track_id,
                title=title,
                artist=artist,
                album=album,
                duration_ms=duration_ms,
                artwork_url=art,
                isrc=str(ann.get("isrc") or ""),
                raw=ann,
            ))
        return len(items)

    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:154.0) Gecko/20100101 Firefox/154.0",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers=browser_headers) as client:
        # Visiting the public share page initializes Pandora's cookie jar,
        # including the CSRF token used by its same-origin REST calls.
        try:
            share_response = await client.get(
                share_url,
                headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
            )
        except httpx.HTTPError as exc:
            raise ValueError("Pandora could not open this shared playlist.") from exc
        if share_response.status_code >= 400:
            raise ValueError(f"Pandora could not open this shared playlist ({share_response.status_code}).")

        csrf_token = client.cookies.get("csrftoken")
        if not csrf_token:
            # A few Pandora edge responses do not set it on the playlist GET.
            # Hitting the site root is enough to establish the same anonymous
            # browser cookie without involving a user login.
            try:
                await client.get("https://www.pandora.com/", headers={"Accept": "text/html,application/xhtml+xml"})
            except httpx.HTTPError:
                pass
            csrf_token = client.cookies.get("csrftoken")
        if not csrf_token:
            raise ValueError("Pandora did not establish an anonymous browser session (missing CSRF token).")

        common_api_headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://www.pandora.com",
            "Referer": str(share_response.url),
            "X-CsrfToken": csrf_token,
        }

        # Reproduce Pandora Web's anonymous bootstrap exactly: the static web
        # client token authorizes anonymousLogin, which returns the ephemeral
        # session authToken used by getTracks.
        try:
            login_response = await client.post(
                anonymous_login_endpoint,
                json={},
                headers={**common_api_headers, "X-AuthToken": PANDORA_WEB_BOOTSTRAP_AUTH},
            )
        except httpx.HTTPError as exc:
            raise ValueError("Pandora could not create an anonymous session for this playlist.") from exc

        if login_response.status_code >= 400:
            detail = ""
            try:
                err = login_response.json()
                if isinstance(err, dict):
                    detail = str(err.get("message") or err.get("errorString") or err.get("error") or "").strip()
            except (ValueError, TypeError):
                pass
            suffix = f": {detail[:180]}" if detail else ""
            raise ValueError(f"Pandora anonymous session failed ({login_response.status_code}){suffix}.")

        try:
            login_payload = login_response.json()
        except ValueError as exc:
            raise ValueError("Pandora returned an unexpected anonymous-session response.") from exc
        if not isinstance(login_payload, dict):
            raise ValueError("Pandora returned an unexpected anonymous-session response.")

        anonymous_auth_token = str(login_payload.get("authToken") or "").strip()
        if not anonymous_auth_token:
            raise ValueError("Pandora did not return an anonymous authorization token.")

        # First page is always requested with playlistVersion=0. Pandora returns
        # the current version in that response; subsequent pages must echo it.
        while True:
            request_payload = {
                "request": {
                    "pandoraId": pandora_id,
                    "playlistVersion": playlist_version if offset else 0,
                    "offset": offset,
                    "limit": limit,
                    "annotationLimit": limit,
                    "allowedTypes": ["TR", "AM"],
                    "bypassPrivacyRules": True,
                }
            }
            try:
                response = await client.post(
                    tracks_endpoint,
                    json=request_payload,
                    headers={**common_api_headers, "X-AuthToken": anonymous_auth_token},
                )
            except httpx.HTTPError as exc:
                raise ValueError("Pandora could not read this shared playlist.") from exc

            if response.status_code >= 400:
                detail_parts: List[str] = []
                try:
                    err = response.json()
                    if isinstance(err, dict):
                        for key in ("message", "errorString", "error", "errorCode", "code"):
                            value = err.get(key)
                            if value not in (None, ""):
                                text = str(value).strip()
                                if text and text not in detail_parts:
                                    detail_parts.append(text)
                except (ValueError, TypeError):
                    pass
                suffix = f": {' / '.join(detail_parts)[:220]}" if detail_parts else ""
                raise ValueError(f"Pandora could not read this shared playlist ({response.status_code}){suffix}.")

            try:
                payload = response.json()
            except ValueError as exc:
                raise ValueError("Pandora returned an unexpected playlist response.") from exc
            if not isinstance(payload, dict):
                raise ValueError("Pandora returned an unexpected playlist response.")

            page_count = consume_payload(payload)
            if page_count <= 0:
                break

            offset += page_count
            if total is not None and offset >= total:
                break
            if page_count < limit:
                break
            if offset > 10000:
                break

    if not tracks:
        raise ValueError("Pandora opened the shared playlist but returned no readable tracks.")
    return name, total, tracks


def track_identity_keys(track: ImportedTrack) -> set[str]:
    keys = {f"text:{_norm(track.title)}|{_norm(track.artist)}"}
    if track.yt_video_id:
        keys.add(f"yt:{track.yt_video_id}")
    return keys


def _youtube_import_variants(track: ImportedTrack) -> List[ImportedTrack]:
    """Build plausible metadata variants for messy YouTube playlist rows.

    Regular YouTube playlists often contain uploads whose channel name is not the
    recording artist and whose title is formatted as "Artist - Song (lyrics)".
    YT Music search is much better at canonicalising those rows if we try both the
    supplied metadata and a conservative artist/title split from the video title.
    """
    variants: List[ImportedTrack] = [track]
    title = html_lib.unescape(track.title or "").strip()

    # Only split on separators that strongly imply "artist - title".  Avoid
    # colon because soundtrack/classical titles use it heavily.
    split = re.match(r"^\s*(.{2,80}?)\s+(?:-|–|—)\s+(.{2,180}?)\s*$", title)
    if split:
        inferred_artist = split.group(1).strip()
        inferred_title = split.group(2).strip()
        if inferred_artist and inferred_title:
            variants.append(ImportedTrack(
                source=track.source,
                source_track_id=track.source_track_id,
                title=inferred_title,
                artist=inferred_artist,
                album=track.album,
                duration_ms=track.duration_ms,
                artwork_url=track.artwork_url,
                isrc=track.isrc,
                yt_video_id=track.yt_video_id,
                raw=track.raw,
            ))

    # De-duplicate variants after normalization.
    unique: List[ImportedTrack] = []
    seen: set[str] = set()
    for item in variants:
        key = f"{_norm(item.title)}|{_norm(item.artist)}"
        if key and key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def _match_score_for_variants(variants: List[ImportedTrack], candidate: Dict[str, Any]) -> float:
    score = max((_candidate_score(variant, candidate) for variant in variants), default=0.0)

    # Prefer actual YT Music song results carrying album metadata over generic
    # video-like matches when the textual match is otherwise comparable.  This
    # is what gives imported playlists proper square release artwork.
    if candidate.get("album"):
        score += 0.025
    return min(score, 1.0)


def match_track(track: ImportedTrack) -> Dict[str, Any]:
    from .integrations.ytmusic import search_ytmusic

    variants = _youtube_import_variants(track) if track.source == "ytmusic" else [track]

    # Search several conservative forms.  This matters for ordinary YouTube
    # playlists where the uploader/channel can be unrelated to the artist.
    queries: List[str] = []
    for variant in variants:
        for query in (
            " ".join(part for part in [variant.title, variant.artist] if part).strip(),
            variant.title.strip(),
        ):
            if query and query not in queries:
                queries.append(query)

    results_by_id: Dict[str, Dict[str, Any]] = {}
    for query in queries[:4]:
        try:
            rows = (search_ytmusic(query, song_limit=8, album_limit=0) or {}).get("songs") or []
        except Exception:
            rows = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            vid = str(row.get("video_id") or row.get("videoId") or "").strip()
            key = vid or f"{_norm(str(row.get('title') or ''))}|{_norm(str(row.get('artist') or ''))}"
            if key and key not in results_by_id:
                results_by_id[key] = row

    scored = sorted(
        ((_match_score_for_variants(variants, row), row) for row in results_by_id.values()),
        key=lambda item: item[0],
        reverse=True,
    )

    if not scored:
        # If YT Music search is temporarily unavailable, preserve a valid source
        # video as a review candidate rather than silently dropping the track.
        if track.source == "ytmusic" and track.yt_video_id:
            fallback = {
                "title": track.title,
                "artist": track.artist,
                "album": track.album,
                "duration_seconds": int(track.duration_ms / 1000) if track.duration_ms else 0,
                "thumbnail_url": track.artwork_url,
                "video_id": track.yt_video_id,
            }
            return {
                "status": "review",
                "confidence": 0.5,
                "candidate": _yt_candidate_payload(fallback),
                "alternatives": [],
            }
        return {"status": "unmatched", "confidence": 0.0, "candidate": None, "alternatives": []}

    best_score, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0

    # YouTube imports are intrinsically noisier than native YT Music playlists,
    # so require a strong canonical match before accepting without review.
    if best_score >= 0.86 and (best_score - second >= 0.05 or best_score >= 0.94):
        status = "matched"
    elif best_score >= 0.66:
        status = "review"
    else:
        status = "unmatched"

    return {
        "status": status,
        "confidence": round(best_score, 4),
        "candidate": _yt_candidate_payload(best) if status != "unmatched" else None,
        "alternatives": [
            _yt_candidate_payload(row) | {"confidence": round(score, 4)}
            for score, row in scored[:3]
        ],
    }
