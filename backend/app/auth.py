from __future__ import annotations

from datetime import datetime, timedelta
import os

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

from .db import SessionLocal
from .models import User, SessionToken

SESSION_COOKIE = "mr_session"


def cookie_secure() -> bool:
    return os.getenv("HELIX_COOKIE_SECURE", "true").strip().lower() in {"1", "true", "yes", "on"}


def session_max_age_seconds() -> int:
    try:
        return max(3600, int(os.getenv("HELIX_SESSION_MAX_AGE_SECONDS", str(60 * 60 * 24 * 30))))
    except Exception:
        return 60 * 60 * 24 * 30


def _get_session_token_from_cookie(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)

def get_current_user(request: Request) -> User:
    # IMPORTANT: do NOT depend on request-scoped get_db here.
    # We intentionally use a short-lived session so authentication never holds a DB connection
    # across slow awaits in downstream request handlers.
    token = _get_session_token_from_cookie(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    db = SessionLocal()
    try:
        stmt = select(SessionToken).where(SessionToken.token == token)
        session = db.execute(stmt).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=401, detail="Invalid session")

        now = datetime.utcnow()
        if session.created_at and session.created_at < now - timedelta(seconds=session_max_age_seconds()):
            db.delete(session)
            db.commit()
            raise HTTPException(status_code=401, detail="Session expired")

        user = db.get(User, session.user_id)
        if not user:
            db.delete(session)
            db.commit()
            raise HTTPException(status_code=401, detail="Invalid session")
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User is disabled")

        session.last_seen_at = now
        db.add(session)
        db.commit()
        # SQLAlchemy expires ORM instances on commit by default. If we return a
        # committed instance after closing the session, attribute access can try
        # to lazy-refresh and crash with DetachedInstanceError.
        # Reload the user fields we need, then detach for safe use downstream.
        db.refresh(user)
        db.expunge(user)
        return user
    finally:
        db.close()

def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user
