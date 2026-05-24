from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import SESSION_COOKIE, get_current_user
from ..db import get_db
from ..models import User
from ..api_schemas.auth import LoginRequest, MeResponse, SetupRequest
from ..services.accounts import authenticate_user, create_initial_admin, setup_enabled as setup_is_enabled

router = APIRouter(tags=["auth"])

COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # set True behind HTTPS
        path="/",
        max_age=COOKIE_MAX_AGE_SECONDS,
    )


@router.post("/setup", response_model=MeResponse)
def setup(payload: SetupRequest, response: Response, db: Session = Depends(get_db)):
    if not setup_is_enabled(db):
        raise HTTPException(status_code=403, detail="Setup is disabled")

    existing = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user, token = create_initial_admin(db, username=payload.username, password=payload.password)
    _set_session_cookie(response, token)
    return MeResponse(id=user.id, username=user.username, role=user.role)


@router.get("/setup/enabled")
def setup_enabled(db: Session = Depends(get_db)):
    return {"enabled": setup_is_enabled(db)}


@router.post("/auth/login", response_model=MeResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user, token = authenticate_user(db, username=payload.username, password=payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    _set_session_cookie(response, token)
    return MeResponse(id=user.id, username=user.username, role=user.role)


@router.post("/auth/logout")
def logout(response: Response, user: User = Depends(get_current_user)):
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)):
    return MeResponse(id=user.id, username=user.username, role=user.role)
