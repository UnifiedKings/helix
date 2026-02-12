from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import User
from .security import decode_token
from .settings import settings

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_token_from_cookie(request: Request) -> str | None:
    return request.cookies.get(settings.COOKIE_NAME)

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = get_token_from_cookie(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        payload = decode_token(token)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User inactive or missing")
    return user

def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return user
