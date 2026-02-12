from __future__ import annotations

import secrets
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)

def new_session_token() -> str:
    # URL-safe random token
    return secrets.token_urlsafe(48)
