"""Custom playlist cover routes; registered before the existing collage-cover route."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.responses import FileResponse
from sqlalchemy.orm import Session
from ..auth import get_current_user
from ..db import get_db
from ..models import User
from ..playlist_custom_covers import custom_cover_path, has_custom_cover, save_custom_cover, remove_custom_cover
from .playlists import _resolve_user_playlist, _to_playlist_response, playlist_cover

router = APIRouter(prefix='/api/playlists', tags=['playlists'])


def _owned(db: Session, user: User, playlist_id: str):
    p = _resolve_user_playlist(db, user.id, playlist_id)
    if p is None:
        raise HTTPException(status_code=404, detail='Playlist not found')
    return p


@router.get('/{playlist_id}/cover')
async def get_custom_or_generated_cover(playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _owned(db, user, playlist_id)
    if has_custom_cover(p.id):
        return FileResponse(custom_cover_path(p.id), media_type='image/jpeg', headers={'Cache-Control': 'private, no-cache, must-revalidate'})
    # Preserve the exact existing automatic playlist collage generation path.
    return await playlist_cover(p.id, db=db, user=user)


@router.post('/{playlist_id}/cover')
async def upload_playlist_cover(playlist_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _owned(db, user, playlist_id)
    # Raw image upload, matching the existing station artwork API.
    if request.headers.get('content-type', '').split(';', 1)[0].lower() not in {'image/png', 'image/jpeg', 'image/webp'}:
        raise HTTPException(status_code=415, detail='Upload a PNG, JPEG, or WebP image')
    data = await request.body()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail='Cover image exceeds 10 MB')
    try:
        save_custom_cover(p.id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    p.updated_at = datetime.utcnow()
    db.commit()
    # Return stable response with updated cache-busting URL.
    return _to_playlist_response(p, 0).model_dump()


@router.delete('/{playlist_id}/cover')
def reset_playlist_cover(playlist_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    p = _owned(db, user, playlist_id)
    remove_custom_cover(p.id)
    p.updated_at = datetime.utcnow()
    db.commit()
    return _to_playlist_response(p, 0).model_dump()
