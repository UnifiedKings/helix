from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from ..deps import get_db
from ..models import User
from ..schemas import SetupRequest, UserOut
from ..security import hash_password

router = APIRouter(prefix="/setup", tags=["setup"])

@router.get("/status")
def setup_status(db: Session = Depends(get_db)):
    any_user = db.query(User).first()
    return {"needs_setup": any_user is None}

@router.post("", response_model=UserOut)
def setup_first_admin(payload: SetupRequest, db: Session = Depends(get_db)):
    # Only allow if there are no users
    if db.query(User).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Setup already completed")

    existing = db.query(User).filter(User.username == payload.username).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already exists")

    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role="admin",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, username=user.username, role=user.role, is_active=user.is_active)
