from __future__ import annotations

import os
import logging
from typing import Any
from fastapi import Body, FastAPI, Depends, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from pathlib import Path
from fastapi.staticfiles import StaticFiles

from .db import init_db, get_db, SessionLocal
from .models import User, SessionToken
from .schemas import (
    SetupRequest,
    LoginRequest,
    MeResponse,
    AdminCreateUserRequest,
    AdminUserResponse,
    AdminUpdateUserRequest,
)
from .security import hash_password, verify_password, new_session_token
from .auth import SESSION_COOKIE, get_current_user, require_admin
from .settings_store import get_settings, patch_settings
from app.integrations.slskd_http import SlskdClient
from .routers.search import router as search_router
from .routers.player import router as player_router
from .routers.ytmusic import router as ytmusic_router
from .routers.album import router as album_router
from .routers.stations import router as stations_router
from .routers.likes import router as likes_router
from .routers.dislikes import router as dislikes_router
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    force=True,  # key: overrides existing logging config
)


logging.basicConfig(level=getattr(logging, os.getenv("HELIX_LOG_LEVEL","INFO").upper(), logging.INFO))

app = FastAPI(title="Helix Backend (WIP)", version="0.1.0")

FRONTEND_ORIGIN = os.getenv("MR_FRONTEND_ORIGIN", "http://localhost:8080")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def _startup():
    init_db()

    # Start background download/finalize workers (YouTube Music fulfillment).
    from .download_manager import DOWNLOAD_MANAGER
    from .settings_store import get_settings

    def _settings_getter():
        db = SessionLocal()
        try:
            return get_settings(db)
        finally:
            db.close()

    DOWNLOAD_MANAGER.set_settings_getter(_settings_getter)
    DOWNLOAD_MANAGER.start()

@app.on_event("startup")
def validate_slskd():
    # Keep this sanity check because the fulfillment engine is expected to talk to slskd.
    client = SlskdClient()
    try:
        client.ping()
    except Exception as e:
        raise RuntimeError(f"Cannot reach slskd: {e}")


app.include_router(search_router)
app.include_router(album_router)
app.include_router(player_router)
app.include_router(ytmusic_router)
app.include_router(stations_router)
app.include_router(likes_router)
app.include_router(dislikes_router)

def _user_count(db: Session) -> int:
    return db.execute(select(func.count(User.id))).scalar_one()

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/setup", response_model=MeResponse)
def setup(payload: SetupRequest, response: Response, db: Session = Depends(get_db)):
    # Only works when no users exist
    if _user_count(db) > 0:
        raise HTTPException(status_code=403, detail="Setup is disabled")

    existing = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(username=payload.username, password_hash=hash_password(payload.password), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = new_session_token()
    sess = SessionToken(token=token, user_id=user.id)
    db.add(sess)
    db.commit()

    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # set True behind HTTPS
        path="/",
        max_age=60 * 60 * 24 * 30,  # 30 days
    )
    return MeResponse(id=user.id, username=user.username, role=user.role)

@app.get("/setup/enabled")
def setup_enabled(db: Session = Depends(get_db)):
    return {"enabled": _user_count(db) == 0}

@app.post("/auth/login", response_model=MeResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = new_session_token()
    sess = SessionToken(token=token, user_id=user.id)
    db.add(sess)
    db.commit()

    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
        max_age=60 * 60 * 24 * 30,
    )
    return MeResponse(id=user.id, username=user.username, role=user.role)

@app.post("/auth/logout")
def logout(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Best-effort: clear cookie (session cleanup could be added later)
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"ok": True}

@app.get("/auth/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)):
    return MeResponse(id=user.id, username=user.username, role=user.role)


@app.get("/settings")
def get_public_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Read-only global settings for the authenticated UI."""
    return get_settings(db)

# ---------------- Admin ----------------

@app.post("/admin/users", response_model=AdminUserResponse)
def admin_create_user(payload: AdminCreateUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Invalid role")

    existing = db.execute(select(User).where(User.username == payload.username)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return AdminUserResponse(id=user.id, username=user.username, role=user.role, is_active=user.is_active)

@app.get("/admin/users", response_model=list[AdminUserResponse])
def admin_list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
    return [AdminUserResponse(id=u.id, username=u.username, role=u.role, is_active=u.is_active) for u in users]

@app.patch("/admin/users/{user_id}", response_model=AdminUserResponse)
def admin_update_user(user_id: str, payload: AdminUpdateUserRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="User not found")

    if payload.is_active is not None:
        u.is_active = payload.is_active
    if payload.role is not None:
        if payload.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="role must be 'admin' or 'user'")
        u.role = payload.role

    db.commit()
    db.refresh(u)
    return AdminUserResponse(id=u.id, username=u.username, role=u.role, is_active=u.is_active)

@app.get("/admin/settings")
def admin_get_settings(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return get_settings(db)

@app.patch("/admin/settings")
def admin_patch_settings(
    payload: dict[str, Any] = Body(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return patch_settings(db, payload)

# --- Serve frontend (single-container mode) ---
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
