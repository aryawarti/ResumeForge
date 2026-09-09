"""JWT authentication with server-side revocable refresh tokens.

Access tokens are short-lived and stateless. Refresh tokens are long-lived and
stateful: only a hash is stored, and each use rotates the token and revokes
its predecessor. Rotation is what makes a stolen refresh token detectable --
if an old token is presented after rotation, something has gone wrong.

One deliberate deviation for Server-Sent Events. ``EventSource`` cannot set an
Authorization header, so the progress stream is authorised with a short-lived,
single-purpose ticket passed as a query parameter. The ticket is scoped to one
generation, expires in a minute, and cannot be used against any other endpoint.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import Settings, get_settings
from .db.models import RefreshToken, User, utcnow
from .db.session import get_session

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=30)
SSE_TICKET_TTL = timedelta(minutes=1)

ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False)


# --- passwords ------------------------------------------------------------


def _prepare(password: str) -> bytes:
    """Reduce a password to a fixed-length input for bcrypt.

    bcrypt silently ignores everything past 72 bytes, which would make two
    long passwords sharing a prefix equivalent. Hashing first means the full
    password always contributes, and base64 keeps the digest free of the NUL
    bytes bcrypt also truncates on.
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prepare(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prepare(password), hashed.encode("ascii"))
    except ValueError:
        # A malformed stored hash must read as "wrong password", not a 500.
        return False


# --- tokens ---------------------------------------------------------------


def _encode(payload: dict[str, Any], settings: Settings) -> str:
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def _decode(token: str, settings: Settings) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="token expired"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        ) from exc


def create_access_token(user_id: str, settings: Settings) -> str:
    now = datetime.now(timezone.utc)
    return _encode(
        {"sub": user_id, "type": "access", "iat": now, "exp": now + ACCESS_TTL},
        settings,
    )


def create_sse_ticket(user_id: str, generation_id: str, settings: Settings) -> str:
    """Mint a token usable only for one generation's event stream."""
    now = datetime.now(timezone.utc)
    return _encode(
        {
            "sub": user_id,
            "type": "sse",
            "gen": generation_id,
            "iat": now,
            "exp": now + SSE_TICKET_TTL,
        },
        settings,
    )


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_refresh_token(
    session: AsyncSession, user_id: str, user_agent: str = ""
) -> str:
    raw = secrets.token_urlsafe(48)
    session.add(
        RefreshToken(
            user_id=user_id,
            token_hash=_hash_token(raw),
            expires_at=utcnow() + REFRESH_TTL,
            user_agent=user_agent[:255],
        )
    )
    await session.commit()
    return raw


async def rotate_refresh_token(
    session: AsyncSession, raw: str, user_agent: str = ""
) -> tuple[User, str]:
    """Consume a refresh token and issue its replacement.

    Rotation, not reuse: the presented token is revoked in the same
    transaction that issues the new one.
    """
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw))
    )
    token = result.scalar_one_or_none()
    if token is None or not token.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="refresh token is invalid or has been revoked",
        )

    user = await session.get(User, token.user_id)
    if user is None or user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account unavailable"
        )

    token.revoked_at = utcnow()
    replacement = secrets.token_urlsafe(48)
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_hash_token(replacement),
            expires_at=utcnow() + REFRESH_TTL,
            user_agent=user_agent[:255],
        )
    )
    await session.commit()
    return user, replacement


async def revoke_refresh_token(session: AsyncSession, raw: str) -> None:
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == _hash_token(raw))
    )
    token = result.scalar_one_or_none()
    if token and token.revoked_at is None:
        token.revoked_at = utcnow()
        await session.commit()


# --- dependencies ---------------------------------------------------------


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated"
        )
    payload = _decode(credentials.credentials, settings)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="wrong token type"
        )
    user = await session.get(User, payload["sub"])
    if user is None or user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account unavailable"
        )
    return user


async def sse_user(
    request: Request,
    generation_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> User:
    """Authorise an EventSource connection via its one-shot ticket."""
    ticket = request.query_params.get("ticket")
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing stream ticket"
        )
    payload = _decode(ticket, settings)
    if payload.get("type") != "sse" or payload.get("gen") != generation_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ticket is not valid for this stream",
        )
    user = await session.get(User, payload["sub"])
    if user is None or user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="account unavailable"
        )
    return user
