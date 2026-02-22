from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import get_current_user
from ..models import User
from ..integrations.ytmusic import get_album_full


router = APIRouter(prefix="/api", tags=["album"])


@router.get("/album/{browse_id}")
def album_view(browse_id: str, user: User = Depends(get_current_user)):
    bid = (browse_id or "").strip()
    if not bid:
        raise HTTPException(status_code=400, detail="browse_id is required")

    data = get_album_full(bid)
    print(data)
    if not data:
        raise HTTPException(status_code=404, detail="Album not found on YouTube Music.")

    return data
