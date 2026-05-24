from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from ..auth import get_current_user
from ..db import SessionLocal
from ..settings_store import get_settings
from ..integrations.subsonic import SubsonicClient
from ..cache import TTLCache

LOG = logging.getLogger("helix.art")

# Cover art is requested frequently by now playing, queue views, and recompositions.
# Cache bytes by (cover_id,size) to reduce repeated Subsonic round trips.
_COVER_ART_CACHE: TTLCache[bytes] = TTLCache(max_items=512)
_COVER_ART_TTL_SECONDS = 60 * 60 * 24

router = APIRouter(prefix="/api/art", tags=["art"])


def _load_settings_short() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        return dict(get_settings(db) or {})
    finally:
        db.close()


def _infer_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


async def _subsonic_client_from_settings(settings: Dict[str, Any]) -> SubsonicClient:
    base_url = str(settings.get("subsonic_base_url") or "").strip()
    username = str(settings.get("subsonic_username") or "").strip()
    password = str(settings.get("subsonic_password") or "").strip()
    if not base_url or not username or not password:
        raise HTTPException(
            status_code=400,
            detail="Subsonic settings incomplete. Set base_url, username, password in Admin Settings.",
        )
    client_name = str(settings.get("subsonic_client_name") or "Helix")
    api_version = str(settings.get("subsonic_api_version") or "1.16.1")
    timeout_s = _infer_int(settings.get("subsonic_timeout_s"), 20) or 20
    return SubsonicClient(
        base_url=base_url,
        username=username,
        password=password,
        client_name=client_name,
        api_version=api_version,
        timeout_s=timeout_s,
    )


def _guess_content_type(data: bytes) -> str:
    # Minimal magic-byte sniffing so browsers display properly.
    if not data:
        return "application/octet-stream"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


@router.get("/subsonic/{cover_id}")
async def subsonic_cover_art(
    cover_id: str,
    size: int = Query(512, ge=32, le=2048),
    user=Depends(get_current_user),
):
    """
    Proxy Subsonic cover art through Helix, so the frontend can use stable internal URLs.

    Authenticated endpoint.
    """
    cid = (cover_id or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="cover_id is required")

    cache_key = f"{cid}:{int(size)}"
    cached = _COVER_ART_CACHE.get(cache_key)
    if cached:
        ctype = _guess_content_type(cached)
        return Response(
            content=cached,
            media_type=ctype,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    settings = _load_settings_short()
    client: Optional[SubsonicClient] = None
    try:
        client = await _subsonic_client_from_settings(settings)
        data = await client.fetch_cover_art_bytes(cid, size=size)
        if not data:
            raise HTTPException(status_code=404, detail="Cover art not found")
        _COVER_ART_CACHE.set(cache_key, data, ttl_seconds=_COVER_ART_TTL_SECONDS)
        ctype = _guess_content_type(data)
        return Response(
            content=data,
            media_type=ctype,
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except HTTPException:
        raise
    except Exception:
        LOG.exception("Subsonic cover art fetch failed for cover_id=%s size=%s", cid, size)
        raise HTTPException(status_code=502, detail="Failed to fetch Subsonic cover art")
    finally:
        if client is not None:
            try:
                await client.close()
            except Exception:
                LOG.exception("Failed to close Subsonic client")
