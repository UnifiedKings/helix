from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import SESSION_COOKIE, cookie_secure, get_current_user
from ..db import get_db
from ..models import User, SessionToken
from ..api_schemas.auth import ChangePasswordRequest, LoginRequest, MeResponse, SetupRequest
from ..services.accounts import authenticate_user, create_initial_admin, setup_enabled as setup_is_enabled
from ..rate_limit import RATE_LIMITER
from ..security import hash_password, verify_password

router = APIRouter(tags=["auth"])

COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def _client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    return forwarded or getattr(getattr(request, "client", None), "host", "") or ""


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
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
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    username_key = (payload.username or "").strip().lower()
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(f"auth-login-ip:{ip}", limit=30, window_s=60 * 10):
        raise HTTPException(status_code=429, detail="Too many login attempts")
    if not RATE_LIMITER.allow(f"auth-login-user:{username_key}:{ip}", limit=8, window_s=60 * 5):
        raise HTTPException(status_code=429, detail="Too many login attempts")

    user, token = authenticate_user(db, username=payload.username, password=payload.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    _set_session_cookie(response, token)
    return MeResponse(id=user.id, username=user.username, role=user.role)


@router.post("/auth/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        row = db.execute(select(SessionToken).where(SessionToken.token == token)).scalar_one_or_none()
        if row:
            db.delete(row)
            db.commit()
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)):
    return MeResponse(id=user.id, username=user.username, role=user.role)


@router.post("/auth/change-password")
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    account = db.get(User, user.id)
    if not account:
        raise HTTPException(status_code=404, detail="User not found")
    if not verify_password(payload.current_password, account.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    if verify_password(payload.new_password, account.password_hash):
        raise HTTPException(status_code=400, detail="New password must be different from the current password")

    account.password_hash = hash_password(payload.new_password)

    # Keep the session used for this password change alive, but revoke all other
    # browser/device sessions for the account.
    current_token = request.cookies.get(SESSION_COOKIE) or ""
    query = db.query(SessionToken).filter(SessionToken.user_id == user.id)
    if current_token:
        query = query.filter(SessionToken.token != current_token)
    query.delete(synchronize_session=False)
    db.add(account)
    db.commit()
    return {"ok": True}
