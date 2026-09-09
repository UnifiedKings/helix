from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import SESSION_COOKIE, cookie_secure, get_current_user
from ..db import get_db
from ..models import User, SessionToken
from ..api_schemas.auth import ChangePasswordRequest, LoginRequest, MeResponse, SetupRequest
from ..services.accounts import (
    authenticate_user,
    create_initial_admin,
    create_session_for_user,
    setup_enabled as setup_is_enabled,
)
from ..rate_limit import RATE_LIMITER
from ..security import hash_password, verify_password
from .. import oidc
from ..login_background import random_login_background

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


def _set_oidc_flow_cookie(response: Response, key: str, value: str) -> None:
    response.set_cookie(
        key=key,
        value=value,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
        path="/",
        max_age=oidc.OIDC_FLOW_MAX_AGE_SECONDS,
    )


def _clear_oidc_flow_cookies(response: Response) -> None:
    for key in (oidc.OIDC_STATE_COOKIE, oidc.OIDC_VERIFIER_COOKIE, oidc.OIDC_NEXT_COOKIE, oidc.OIDC_NONCE_COOKIE):
        response.delete_cookie(key=key, path="/")


def _oidc_error_redirect(detail: str) -> RedirectResponse:
    query = urlencode({"oidc_error": detail[:500]})
    response = RedirectResponse(url=f"/login?{query}", status_code=302)
    _clear_oidc_flow_cookies(response)
    return response


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


@router.get("/auth/oidc/config")
def oidc_config():
    return oidc.public_config()


@router.get("/auth/login-background")
def login_background():
    return random_login_background()


@router.get("/auth/oidc/login")
def oidc_login(
    request: Request,
    next_path: str = Query("/", alias="next"),
):
    ip = _client_ip(request)
    if not RATE_LIMITER.allow(f"auth-oidc-start-ip:{ip}", limit=30, window_s=60 * 10):
        raise HTTPException(status_code=429, detail="Too many login attempts")

    oidc.validate_configuration()
    state, verifier, challenge, nonce = oidc.new_authorization_flow()
    target = oidc.sanitize_next_path(next_path)

    response = RedirectResponse(
        url=oidc.authorization_url(state=state, code_challenge=challenge, nonce=nonce),
        status_code=302,
    )
    _set_oidc_flow_cookie(response, oidc.OIDC_STATE_COOKIE, state)
    _set_oidc_flow_cookie(response, oidc.OIDC_VERIFIER_COOKIE, verifier)
    _set_oidc_flow_cookie(response, oidc.OIDC_NEXT_COOKIE, target)
    _set_oidc_flow_cookie(response, oidc.OIDC_NONCE_COOKIE, nonce)
    return response


@router.get("/auth/oidc/callback", name="oidc_callback")
def oidc_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
):
    if error:
        return _oidc_error_redirect(error_description or error)

    expected_state = request.cookies.get(oidc.OIDC_STATE_COOKIE) or ""
    verifier = request.cookies.get(oidc.OIDC_VERIFIER_COOKIE) or ""
    expected_nonce = request.cookies.get(oidc.OIDC_NONCE_COOKIE) or ""
    next_path = oidc.sanitize_next_path(request.cookies.get(oidc.OIDC_NEXT_COOKIE))

    if not state or not expected_state or not secrets_compare(state, expected_state):
        return _oidc_error_redirect("OIDC login state validation failed")
    if not code or not verifier or not expected_nonce:
        return _oidc_error_redirect("OIDC login flow data is incomplete or expired")

    try:
        token = oidc.exchange_code(code=code, code_verifier=verifier)
        id_claims = oidc.validate_id_token(str(token["id_token"]), expected_nonce=expected_nonce)
        userinfo = oidc.fetch_userinfo(str(token["access_token"]))
        if str(userinfo.get("sub") or "") != str(id_claims.get("sub") or ""):
            raise HTTPException(status_code=401, detail="OIDC user-info subject did not match the ID token")
        user = oidc.resolve_user(db, userinfo)
        session_token = create_session_for_user(db, user=user)
    except HTTPException as exc:
        return _oidc_error_redirect(str(exc.detail))
    except Exception:
        return _oidc_error_redirect("OIDC login failed")

    response = RedirectResponse(url=next_path, status_code=302)
    _set_session_cookie(response, session_token)
    _clear_oidc_flow_cookies(response)
    return response


def secrets_compare(left: str, right: str) -> bool:
    # Import locally so the auth router's import list stays focused on HTTP/auth types.
    import secrets
    return secrets.compare_digest(left, right)


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
