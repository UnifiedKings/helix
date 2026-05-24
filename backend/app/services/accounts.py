from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..models import User, SessionToken
from ..security import hash_password, verify_password, new_session_token


def user_count(db: Session) -> int:
    """Return the number of configured Helix users."""
    return db.execute(select(func.count(User.id))).scalar_one()


def setup_enabled(db: Session) -> bool:
    """Initial setup is allowed only before the first user exists."""
    return user_count(db) == 0


def create_initial_admin(db: Session, *, username: str, password: str) -> tuple[User, str]:
    """Create the first admin user and session token."""
    user = User(username=username, password_hash=hash_password(password), role="admin", is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)

    token = new_session_token()
    db.add(SessionToken(token=token, user_id=user.id))
    db.commit()
    return user, token


def authenticate_user(db: Session, *, username: str, password: str) -> tuple[User | None, str]:
    """Validate credentials and create a new session token on success."""
    user = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(password, user.password_hash):
        return None, ""

    token = new_session_token()
    db.add(SessionToken(token=token, user_id=user.id))
    db.commit()
    return user, token


def create_user(db: Session, *, username: str, password: str, role: str) -> User:
    """Create a regular/admin user. Caller validates role and uniqueness."""
    user = User(username=username, password_hash=hash_password(password), role=role, is_active=True)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def list_users(db: Session) -> list[User]:
    """Return users with newest accounts first."""
    return db.execute(select(User).order_by(User.created_at.desc())).scalars().all()
