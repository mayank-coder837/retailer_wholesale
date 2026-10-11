"""Passwords (argon2id) and tokens (ADR 0007)."""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings

_hasher = PasswordHasher()
JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling, in which tenant, through which node — from a verified access token."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    node_id: uuid.UUID | None
    session_id: uuid.UUID
    client: str = "web"


def create_access_token(principal: Principal, settings: Settings, *, now: datetime | None = None) -> str:
    issued = now or datetime.now(UTC)
    claims = {
        "sub": str(principal.user_id),
        "tid": str(principal.tenant_id),
        "mid": str(principal.membership_id),
        "nid": str(principal.node_id) if principal.node_id else None,
        "sid": str(principal.session_id),
        "cli": principal.client,
        "iat": issued,
        "exp": issued + timedelta(seconds=settings.access_token_ttl_s),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, settings: Settings) -> Principal | None:
    """Returns ``None`` for any invalid, expired or malformed token."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "tid", "mid", "sid", "exp"]},
        )
        return Principal(
            user_id=uuid.UUID(claims["sub"]),
            tenant_id=uuid.UUID(claims["tid"]),
            membership_id=uuid.UUID(claims["mid"]),
            node_id=uuid.UUID(claims["nid"]) if claims.get("nid") else None,
            session_id=uuid.UUID(claims["sid"]),
            client=str(claims.get("cli") or "web"),
        )
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        return None


def new_refresh_token() -> tuple[str, str]:
    """Returns ``(raw_token, sha256_hex)``. Only the hash is stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
