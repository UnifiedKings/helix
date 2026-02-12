from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session
from ..deps import get_db, get_current_user
from ..models import User
from ..schemas import LoginRequest, UserOut
from ..security import verify_password, create_access_token
from ..settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])

@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == payload.username).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(subject=user.id, role=user.role)
    response.set_cookie(
        key=settings.COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,  # set True behind HTTPS
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
    )
    return UserOut(id=user.id, username=user.username, role=user.role, is_active=user.is_active)

@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(key=settings.COOKIE_NAME, path="/")
    return {"ok": True}

@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut(id=user.id, username=user.username, role=user.role, is_active=user.is_active)
