from __future__ import annotations

from fastapi import APIRouter

from .shares import router as shares_router

router = APIRouter(tags=["system"])
router.include_router(shares_router)


@router.get("/health")
def health():
    return {"ok": True}
