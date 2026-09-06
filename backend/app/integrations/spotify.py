from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..models import SpotifyConnection, SpotifyOAuthState
from ..playlist_imports import ImportedTrack

logger = logging.getLogger(__name__)

SPOTIFY_ACCOUNTS_URL = "https://accounts.spotify.com"
SPOTIFY_API_URL = "https://api.spotify.com/v1"

# Read-only scopes needed to list a user's playlists and Liked Songs and to
# read their tracks. Helix never writes to Spotify.
SPOTIFY_SCOPES = "playlist-read-private playlist-read-collaborative user-library-read"

# Refresh the access token a little before it actually expires.
EXPIRY_BUFFER_SECONDS = 60

# Held-once OAuth states are valid for this long.
OAUTH_STATE_TTL_SECONDS = 10 * 60


class SpotifyConfigError(RuntimeError):
    """Raised when Spotify OAuth env configuration is missing."""


class SpotifyAuthRequired(RuntimeError):
    """Raised when the user's Spotify connection is missing or unusable.

    Callers should clear the stored connection and tell the client to connect
    again.
    """


def spotify_client_id() -> str:
    value = (os.getenv("SPOTIFY_CLIENT_ID") or "").strip()
    if not value:
        raise SpotifyConfigError("SPOTIFY_CLIENT_ID is not configured")
    return value


def spotify_client_secret() -> str:
    value = (os.getenv("SPOTIFY_CLIENT_SECRET") or "").strip()
    if not value:
        raise SpotifyConfigError("SPOTIFY_CLIENT_SECRET is not configured")
    return value


def spotify_configured() -> bool:
    return bool(
        (os.getenv("SPOTIFY_CLIENT_ID") or "").strip()
        and (os.getenv("SPOTIFY_CLIENT_SECRET") or "").strip()
    )


def spotify_redirect_uri(request) -> str:
    """Return the OAuth redirect URI for this request.

    Spotify requires the registered redirect URI to match exactly. Admins can
    pin it with SPOTIFY_REDIRECT_URI; otherwise it is derived from the current
    request's scheme and host, which keeps self-hosted setups working behind a
    reverse proxy without extra configuration.
    """
    pinned = (os.getenv("SPOTIFY_REDIRECT_URI") or "").strip()
    if pinned:
        return pinned
    return f"{request.url.scheme}://{request.headers.get('host', 'localhost')}/spotify/auth/callback"


def build_authorize_url(state: str, redirect_uri: str) -> str:
    params = urlencode({
        "client_id": spotify_client_id(),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SPOTIFY_SCOPES,
        "state": state,
        "show_dialog": "true",
    })
    return f"{SPOTIFY_ACCOUNTS_URL}/authorize?{params}"


def _exchange_tokens(payload: Dict[str, str]) -> Dict[str, Any]:
    try:
        response = httpx.post(
            f"{SPOTIFY_ACCOUNTS_URL}/api/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=25,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Spotify token request failed: {exc}") from exc

    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                detail = str(body.get("error_description") or body.get("error") or "")
        except (ValueError, TypeError):
            pass
        suffix = f": {detail[:200]}" if detail else ""
        raise RuntimeError(f"Spotify token request failed ({response.status_code}){suffix}")

    try:
        payload_json = response.json()
    except ValueError as exc:
        raise RuntimeError("Spotify returned an unexpected token response.") from exc
    if not isinstance(payload_json, dict):
        raise RuntimeError("Spotify returned an unexpected token response.")

    access_token = str(payload_json.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Spotify token response did not include an access token.")
    try:
        expires_in = max(60, int(payload_json.get("expires_in") or 3600))
    except (TypeError, ValueError):
        expires_in = 3600
    return {
        "access_token": access_token,
        "refresh_token": str(payload_json.get("refresh_token") or "").strip(),
        "expires_at": datetime.utcnow() + timedelta(seconds=expires_in),
    }


def exchange_code(code: str, redirect_uri: str) -> Dict[str, Any]:
    return _exchange_tokens({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": spotify_client_id(),
        "client_secret": spotify_client_secret(),
    })


def refresh_tokens(refresh_token: str) -> Dict[str, Any]:
    return _exchange_tokens({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": spotify_client_id(),
        "client_secret": spotify_client_secret(),
    })


# --- Pending OAuth state (login CSRF protection) ---


def create_oauth_state(db: Session, user_id: str, redirect_uri: str) -> str:
    import secrets

    # Prune stale states for this user so they cannot accumulate.
    cutoff = datetime.utcnow() - timedelta(seconds=OAUTH_STATE_TTL_SECONDS)
    db.execute(delete(SpotifyOAuthState).where(
        SpotifyOAuthState.user_id == user_id,
        SpotifyOAuthState.created_at < cutoff,
    ))

    state = secrets.token_urlsafe(32)
    row = SpotifyOAuthState(state=state, user_id=user_id, redirect_uri=redirect_uri)
    db.add(row)
    db.commit()
    return state


def consume_oauth_state(db: Session, state: str) -> Optional[Dict[str, Any]]:
    """Resolve and delete a pending OAuth state, enforcing its TTL.

    Returns the captured user id and redirect URI for the state, or None if the
    state is unknown/expired. The DB row is removed in the same operation.
    """
    if not state:
        return None
    row = db.execute(select(SpotifyOAuthState).where(SpotifyOAuthState.state == state)).scalar_one_or_none()
    if not row:
        return None
    captured = {
        "user_id": row.user_id,
        "redirect_uri": row.redirect_uri,
        "created_at": row.created_at,
    }
    db.delete(row)
    db.commit()
    if captured["created_at"] and captured["created_at"] < datetime.utcnow() - timedelta(seconds=OAUTH_STATE_TTL_SECONDS):
        return None
    return captured


# --- Stored connections ---


def get_connection(db: Session, user_id: str) -> Optional[SpotifyConnection]:
    return db.execute(
        select(SpotifyConnection).where(SpotifyConnection.user_id == user_id)
    ).scalar_one_or_none()


def save_connection(
    db: Session,
    user_id: str,
    *,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
    display_name: str = "",
) -> SpotifyConnection:
    row = get_connection(db, user_id)
    now = datetime.utcnow()
    if row is None:
        row = SpotifyConnection(
            user_id=user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            display_name=display_name,
            connected_at=now,
            updated_at=now,
        )
    else:
        row.access_token = access_token
        row.refresh_token = refresh_token
        row.expires_at = expires_at
        row.display_name = display_name
        row.updated_at = now
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def clear_connection(db: Session, user_id: str) -> bool:
    row = get_connection(db, user_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def connection_status(db: Session, user_id: str) -> Dict[str, Any]:
    row = get_connection(db, user_id)
    if row is None:
        return {"connected": False, "display_name": "", "connected_at": None}
    return {
        "connected": True,
        "display_name": row.display_name or "Spotify",
        "connected_at": row.connected_at,
        "configured": spotify_configured(),
    }


def _valid_access_token(db: Session, user_id: str) -> str:
    """Return a usable access token, refreshing the stored one when needed."""
    row = get_connection(db, user_id)
    if row is None or not row.access_token:
        raise SpotifyAuthRequired("Spotify is not connected")

    if row.refresh_token and row.expires_at <= datetime.utcnow() + timedelta(seconds=EXPIRY_BUFFER_SECONDS):
        try:
            renewed = refresh_tokens(row.refresh_token)
        except RuntimeError:
            # A revoked/expired refresh token is the common reason Spotify rejects
            # a refresh; drop the connection so the user can reconnect cleanly.
            clear_connection(db, user_id)
            raise SpotifyAuthRequired("Your Spotify connection expired. Connect Spotify again.") from None
        row = save_connection(
            db,
            user_id,
            access_token=renewed["access_token"],
            refresh_token=renewed.get("refresh_token") or row.refresh_token,
            expires_at=renewed["expires_at"],
            display_name=row.display_name,
        )
    return row.access_token


# --- Spotify Web API ---


def _spotify_get(access_token: str, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{SPOTIFY_API_URL}{path}"
    try:
        response = httpx.get(
            url,
            params=params,
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            timeout=25,
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Spotify API request failed: {exc}") from exc

    if response.status_code == 401:
        raise SpotifyAuthRequired("Your Spotify connection expired. Connect Spotify again.")
    if response.status_code >= 400:
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict) and "error" in body:
                error = body["error"]
                detail = str(error.get("message") or error if isinstance(error, dict) else error)
        except (ValueError, TypeError):
            pass
        suffix = f": {detail[:200]}" if detail else ""
        raise RuntimeError(f"Spotify API request failed ({response.status_code}){suffix}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Spotify returned an unexpected response.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Spotify returned an unexpected response.")
    return payload


def fetch_profile(access_token: str) -> str:
    payload = _spotify_get(access_token, "/me")
    return str(payload.get("display_name") or payload.get("id") or "Spotify")


def _list_items(access_token: str, path: str, limit: int = 50) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    offset = 0
    while True:
        payload = _spotify_get(
            access_token,
            path,
            {"limit": limit, "offset": offset, "market": "from_token"},
        )
        page = payload.get("items") or []
        items.extend(item for item in page if isinstance(item, dict))
        total = payload.get("total")
        offset += len(page)
        if not page or (total is not None and offset >= int(total)) or len(page) < limit or offset > 10000:
            break
    return items


def list_user_playlists(db: Session, user_id: str) -> Dict[str, Any]:
    access_token = _valid_access_token(db, user_id)
    display_name = fetch_profile(access_token)
    playlists: List[Dict[str, Any]] = []

    liked_total = 0
    try:
        liked = _spotify_get(access_token, "/me/tracks", {"limit": 1})
        liked_total = int(liked.get("total") or 0)
    except (RuntimeError, SpotifyAuthRequired, ValueError):
        pass
    playlists.append({"id": "liked", "name": "Liked Songs", "track_count": liked_total, "is_liked": True})

    for item in _list_items(access_token, "/me/playlists", limit=50):
        playlist_id = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not playlist_id or not name:
            continue
        playlists.append({
            "id": playlist_id,
            "name": name,
            "track_count": int(item.get("tracks") and item["tracks"].get("total") or 0),
            "is_liked": False,
        })

    playlists.sort(key=lambda p: (p["is_liked"], p["name"].casefold()))
    return {"display_name": display_name, "playlists": playlists}


def _track_from_spotify_item(item: Dict[str, Any]) -> Optional[ImportedTrack]:
    track = item.get("track")
    if not isinstance(track, dict):
        return None
    # Local/unavailable tracks come back without an id and cannot be matched.
    source_id = str(track.get("id") or "").strip()
    title = str(track.get("name") or "").strip()
    if not title:
        return None
    artists = track.get("artists") or []
    artist_names = [
        str(a.get("name") or "").strip()
        for a in artists
        if isinstance(a, dict) and (a.get("name") or "").strip()
    ]
    artist = ", ".join(artist_names)
    if not artist:
        return None

    album_obj = track.get("album")
    album = ""
    artwork_url = ""
    if isinstance(album_obj, dict):
        album = str(album_obj.get("name") or "").strip()
        images = album_obj.get("images") or []
        if isinstance(images, list):
            for image in sorted(images, key=lambda img: (img.get("height") or 0), reverse=True):
                url = str(image.get("url") or "").strip() if isinstance(image, dict) else ""
                if url:
                    artwork_url = url
                    break

    external_ids = track.get("external_ids") or {}
    isrc = str(external_ids.get("isrc") or "").strip() if isinstance(external_ids, dict) else ""

    duration_ms = 0
    try:
        duration_ms = int(track.get("duration_ms") or 0)
    except (TypeError, ValueError):
        duration_ms = 0

    return ImportedTrack(
        source="spotify",
        source_track_id=source_id,
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        artwork_url=artwork_url,
        isrc=isrc,
        raw=track,
    )


def fetch_playlist_tracks(db: Session, user_id: str, playlist_id: str) -> Dict[str, Any]:
    access_token = _valid_access_token(db, user_id)
    playlist_id = (playlist_id or "").strip()
    name = "Spotify playlist"

    if not playlist_id:
        raise RuntimeError("No Spotify playlist selected.")

    if playlist_id == "liked":
        items = _list_items(access_token, "/me/tracks", limit=50)
        name = "Liked Songs"
    else:
        try:
            detail = _spotify_get(access_token, f"/playlists/{playlist_id}", {"fields": "name"})
            name = str(detail.get("name") or name)
        except RuntimeError:
            pass
        items = _list_items(access_token, f"/playlists/{playlist_id}/tracks", limit=100)

    tracks: List[ImportedTrack] = []
    seen: set[str] = set()
    for item in items:
        track = _track_from_spotify_item(item)
        if track is None:
            continue
        dedupe = track.source_track_id or f"{track.title.casefold()}|{track.artist.casefold()}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        tracks.append(track)

    return {"name": name, "tracks": tracks}