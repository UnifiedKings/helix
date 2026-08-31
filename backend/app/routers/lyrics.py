from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..services.lyrics import LyricsQuery, get_lyrics

router = APIRouter(prefix="/api/lyrics", tags=["lyrics"])


@router.get("")
async def lyrics(
    title: str = Query(..., min_length=1, max_length=500),
    artist: str = Query(..., min_length=1, max_length=500),
    album: str = Query("", max_length=500),
    duration_ms: int = Query(0, ge=0, le=3_600_000),
):
    try:
        return await get_lyrics(
            LyricsQuery(
                title=title,
                artist=artist,
                album=album,
                duration_ms=duration_ms,
            )
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
