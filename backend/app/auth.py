from __future__ import annotations

from datetime import datetime
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select

from .db import get_db
from .models import User, SessionToken

SESSION_COOKIE = "mr_session"

def _get_session_token_from_cookie(request: Request) -> str | None:
    return request.cookies.get(SESSION_COOKIE)

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = _get_session_token_from_cookie(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    stmt = select(SessionToken).where(SessionToken.token == token)
    session = db.execute(stmt).scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")

    user = session.user
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is disabled")

    session.last_seen_at = datetime.utcnow()
    db.add(session)
    db.commit()

    return user

def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return user
