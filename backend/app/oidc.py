from __future__ import annotations

import base64
import hashlib
import os
import secrets
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWTError
from fastapi import HTTPException, Request
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .models import User
from .services.accounts import create_user, user_count


OIDC_STATE_COOKIE = "helix_oidc_state"
OIDC_VERIFIER_COOKIE = "helix_oidc_verifier"
OIDC_NEXT_COOKIE = "helix_oidc_next"
OIDC_NONCE_COOKIE = "helix_oidc_nonce"
OIDC_FLOW_MAX_AGE_SECONDS = 10 * 60


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def enabled() -> bool:
    return _env_bool("HELIX_OIDC_ENABLED", False)


def display_name() -> str:
    return (os.getenv("HELIX_OIDC_DISPLAY_NAME", "Authentik") or "Authentik").strip()


def issuer() -> str:
    return (os.getenv("HELIX_OIDC_ISSUER", "") or "").strip().rstrip("/")


def client_id() -> str:
    return (os.getenv("HELIX_OIDC_CLIENT_ID", "") or "").strip()


def client_secret() -> str:
    return os.getenv("HELIX_OIDC_CLIENT_SECRET", "") or ""


def redirect_uri() -> str:
    return (os.getenv("HELIX_OIDC_REDIRECT_URI", "") or "").strip()


def scopes() -> str:
    raw = (os.getenv("HELIX_OIDC_SCOPES", "openid profile email") or "").strip()
    parts = [part for part in raw.replace(",", " ").split() if part]
    if "openid" not in parts:
        parts.insert(0, "openid")
    return " ".join(dict.fromkeys(parts))


def auto_create_users() -> bool:
    return _env_bool("HELIX_OIDC_AUTO_CREATE_USERS", True)


def auto_link_by_username() -> bool:
    return _env_bool("HELIX_OIDC_AUTO_LINK_BY_USERNAME", False)


def allow_first_user_admin() -> bool:
    return _env_bool("HELIX_OIDC_ALLOW_FIRST_USER_ADMIN", False)


def sync_roles() -> bool:
    return _env_bool("HELIX_OIDC_SYNC_ROLES", False)


def username_claim() -> str:
    return (os.getenv("HELIX_OIDC_USERNAME_CLAIM", "preferred_username") or "preferred_username").strip()


def groups_claim() -> str:
    return (os.getenv("HELIX_OIDC_GROUPS_CLAIM", "groups") or "groups").strip()


def admin_group() -> str:
    return (os.getenv("HELIX_OIDC_ADMIN_GROUP", "") or "").strip()


def public_config() -> dict[str, Any]:
    return {
        "enabled": enabled(),
        "display_name": display_name(),
        "auto_create_users": auto_create_users(),
    }


def validate_configuration() -> None:
    if not enabled():
        raise HTTPException(status_code=404, detail="OIDC login is not enabled")
    missing = []
    if not issuer():
        missing.append("HELIX_OIDC_ISSUER")
    if not client_id():
        missing.append("HELIX_OIDC_CLIENT_ID")
    if not redirect_uri():
        missing.append("HELIX_OIDC_REDIRECT_URI")
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"OIDC is enabled but missing configuration: {', '.join(missing)}",
        )


@lru_cache(maxsize=4)
def discovery_document(configured_issuer: str) -> dict[str, Any]:
    url = f"{configured_issuer.rstrip('/')}/.well-known/openid-configuration"
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not load OIDC discovery document") from exc

    returned_issuer = str(payload.get("issuer") or "").rstrip("/")
    if returned_issuer != configured_issuer.rstrip("/"):
        raise HTTPException(status_code=502, detail="OIDC discovery issuer did not match HELIX_OIDC_ISSUER")

    for field in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint", "jwks_uri"):
        if not payload.get(field):
            raise HTTPException(status_code=502, detail=f"OIDC discovery document is missing {field}")
    return payload


def new_authorization_flow() -> tuple[str, str, str, str]:
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    nonce = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return state, verifier, challenge, nonce


def authorization_url(*, state: str, code_challenge: str, nonce: str) -> str:
    validate_configuration()
    discovery = discovery_document(issuer())
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id(),
            "redirect_uri": redirect_uri(),
            "scope": scopes(),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "nonce": nonce,
        }
    )
    return f"{discovery['authorization_endpoint']}?{query}"


def exchange_code(*, code: str, code_verifier: str) -> dict[str, Any]:
    validate_configuration()
    discovery = discovery_document(issuer())
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(),
        "client_id": client_id(),
        "code_verifier": code_verifier,
    }

    auth = None
    secret = client_secret()
    if secret:
        auth = (client_id(), secret)

    try:
        with httpx.Client(timeout=15.0, follow_redirects=False) as client:
            response = client.post(
                str(discovery["token_endpoint"]),
                data=data,
                auth=auth,
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            token = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="OIDC authorization code exchange failed") from exc

    if not token.get("access_token"):
        raise HTTPException(status_code=502, detail="OIDC provider did not return an access token")
    if not token.get("id_token"):
        raise HTTPException(status_code=502, detail="OIDC provider did not return an ID token")
    return token


SAFE_ID_TOKEN_ALGORITHMS = {
    "RS256", "RS384", "RS512",
    "PS256", "PS384", "PS512",
    "ES256", "ES384", "ES512",
    "EdDSA",
}


def validate_id_token(id_token: str, *, expected_nonce: str) -> dict[str, Any]:
    validate_configuration()
    if not expected_nonce:
        raise HTTPException(status_code=401, detail="OIDC nonce is missing")

    discovery = discovery_document(issuer())
    supported = discovery.get("id_token_signing_alg_values_supported")
    if isinstance(supported, list):
        allowed_algorithms = [str(value) for value in supported if str(value) in SAFE_ID_TOKEN_ALGORITHMS]
    else:
        allowed_algorithms = ["RS256"]

    if not allowed_algorithms:
        raise HTTPException(status_code=502, detail="OIDC provider has no supported ID token signing algorithm")

    try:
        header = jwt.get_unverified_header(id_token)
        algorithm = str(header.get("alg") or "")
    except PyJWTError as exc:
        raise HTTPException(status_code=401, detail="OIDC ID token header is invalid") from exc

    if algorithm not in allowed_algorithms:
        raise HTTPException(status_code=401, detail="OIDC ID token uses an unsupported signing algorithm")

    try:
        jwks_client = PyJWKClient(
            str(discovery["jwks_uri"]),
            cache_keys=True,
            cache_jwk_set=True,
            lifespan=300,
            timeout=10,
        )
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=allowed_algorithms,
            audience=client_id(),
            issuer=str(discovery["issuer"]),
            leeway=30,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_aud": True,
            },
        )
    except PyJWTError as exc:
        raise HTTPException(status_code=401, detail="OIDC ID token validation failed") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not retrieve OIDC signing keys") from exc

    token_nonce = str(claims.get("nonce") or "")
    if not token_nonce or not secrets.compare_digest(token_nonce, expected_nonce):
        raise HTTPException(status_code=401, detail="OIDC nonce validation failed")

    authorized_party = str(claims.get("azp") or "")
    audience = claims.get("aud")
    if authorized_party and authorized_party != client_id():
        raise HTTPException(status_code=401, detail="OIDC authorized party did not match this Helix client")
    if isinstance(audience, list) and len(audience) > 1 and authorized_party != client_id():
        raise HTTPException(status_code=401, detail="OIDC token with multiple audiences did not identify Helix as azp")

    return dict(claims)


def fetch_userinfo(access_token: str) -> dict[str, Any]:
    discovery = discovery_document(issuer())
    try:
        with httpx.Client(timeout=10.0, follow_redirects=False) as client:
            response = client.get(
                str(discovery["userinfo_endpoint"]),
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not retrieve OIDC user information") from exc

    if not payload.get("sub"):
        raise HTTPException(status_code=502, detail="OIDC user information did not contain a subject")
    return payload


def sanitize_next_path(value: str | None) -> str:
    candidate = (value or "").strip()
    if not candidate.startswith("/") or candidate.startswith("//"):
        return "/"
    return candidate[:1024]


def _ensure_identity_table(db: Session) -> None:
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS oidc_identities (
                issuer TEXT NOT NULL,
                subject TEXT NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                username_snapshot TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (issuer, subject),
                UNIQUE (issuer, user_id)
            )
            """
        )
    )
    db.commit()


def _mapped_user(db: Session, subject: str) -> User | None:
    _ensure_identity_table(db)
    user_id = db.execute(
        text(
            "SELECT user_id FROM oidc_identities "
            "WHERE issuer = :issuer AND subject = :subject"
        ),
        {"issuer": issuer(), "subject": subject},
    ).scalar_one_or_none()
    if not user_id:
        return None

    user = db.get(User, str(user_id))
    if user:
        return user

    # A Helix account may have been deleted after the identity was linked.
    db.execute(
        text(
            "DELETE FROM oidc_identities "
            "WHERE issuer = :issuer AND subject = :subject"
        ),
        {"issuer": issuer(), "subject": subject},
    )
    db.commit()
    return None


def _link_identity(db: Session, *, subject: str, user: User, username_snapshot: str) -> None:
    _ensure_identity_table(db)
    db.execute(
        text(
            """
            INSERT INTO oidc_identities (issuer, subject, user_id, username_snapshot)
            VALUES (:issuer, :subject, :user_id, :username_snapshot)
            ON CONFLICT(issuer, subject) DO UPDATE SET
                user_id = excluded.user_id,
                username_snapshot = excluded.username_snapshot,
                updated_at = CURRENT_TIMESTAMP
            """
        ),
        {
            "issuer": issuer(),
            "subject": subject,
            "user_id": user.id,
            "username_snapshot": username_snapshot,
        },
    )
    db.commit()


def _claim_groups(userinfo: dict[str, Any]) -> list[str]:
    value = userinfo.get(groups_claim())
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _claim_username(userinfo: dict[str, Any]) -> str:
    candidates = [
        userinfo.get(username_claim()),
        userinfo.get("preferred_username"),
        userinfo.get("nickname"),
    ]
    email = str(userinfo.get("email") or "").strip()
    if email and "@" in email:
        candidates.append(email.split("@", 1)[0])
    candidates.append(f"oidc-{str(userinfo.get('sub') or '')[:24]}")

    for value in candidates:
        text_value = str(value or "").strip()
        if text_value:
            return text_value[:64]
    raise HTTPException(status_code=502, detail="OIDC provider did not return a usable username")


def _desired_role(*, groups: list[str], is_first_user: bool) -> str:
    if is_first_user:
        if not allow_first_user_admin():
            raise HTTPException(
                status_code=403,
                detail=(
                    "Complete initial Helix setup with a local admin before using OIDC auto-provisioning. "
                    "Set HELIX_OIDC_ALLOW_FIRST_USER_ADMIN=true only if you intentionally want the first "
                    "successful OIDC login to claim the initial administrator account."
                ),
            )
        return "admin"
    configured_admin_group = admin_group()
    if configured_admin_group and configured_admin_group in groups:
        return "admin"
    return "user"


def resolve_user(db: Session, userinfo: dict[str, Any]) -> User:
    subject = str(userinfo.get("sub") or "").strip()
    if not subject:
        raise HTTPException(status_code=502, detail="OIDC subject is missing")

    username = _claim_username(userinfo)
    groups = _claim_groups(userinfo)

    mapped = _mapped_user(db, subject)
    if mapped:
        if not mapped.is_active:
            raise HTTPException(status_code=403, detail="This Helix account is disabled")
        if sync_roles() and admin_group():
            desired = _desired_role(groups=groups, is_first_user=False)
            if mapped.role != desired:
                mapped.role = desired
                db.add(mapped)
                db.commit()
                db.refresh(mapped)
        _link_identity(db, subject=subject, user=mapped, username_snapshot=username)
        return mapped

    existing = db.execute(
        select(User).where(func.lower(User.username) == username.lower())
    ).scalar_one_or_none()

    if existing:
        if not auto_link_by_username():
            raise HTTPException(
                status_code=409,
                detail=(
                    "A Helix account already uses this username. "
                    "Set HELIX_OIDC_AUTO_LINK_BY_USERNAME=true only if you intend "
                    "to attach matching Authentik usernames to existing Helix accounts."
                ),
            )
        if not existing.is_active:
            raise HTTPException(status_code=403, detail="This Helix account is disabled")
        _link_identity(db, subject=subject, user=existing, username_snapshot=username)
        return existing

    if not auto_create_users():
        raise HTTPException(status_code=403, detail="No linked Helix account exists for this Authentik user")

    first_user = user_count(db) == 0
    role = _desired_role(groups=groups, is_first_user=first_user)
    random_password = secrets.token_urlsafe(48)
    user = create_user(db, username=username, password=random_password, role=role)
    _link_identity(db, subject=subject, user=user, username_snapshot=username)
    return user
