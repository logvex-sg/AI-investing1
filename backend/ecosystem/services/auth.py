"""Authentication and session handling.

Humans authenticate with a password; the API issues a signed session token.
No LLM ever receives a password, a token, or a signing key. Passwords are
stored only as bcrypt hashes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.config import get_settings
from ecosystem.db.models.enums import EventCategory, UserRole
from ecosystem.db.models.identity import User
from ecosystem.services import events

SESSION_TTL_SECONDS = 12 * 3600
_MIN_PASSWORD_LENGTH = 10


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    if len(password) < _MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"Password must be at least {_MIN_PASSWORD_LENGTH} characters."
        )
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except (ValueError, TypeError):
        return False


def _sign(payload: bytes) -> str:
    key = get_settings().secret_key.encode()
    return base64.urlsafe_b64encode(hmac.new(key, payload, hashlib.sha256).digest()).decode()


def create_session_token(user: User) -> str:
    """A compact, signed token. Stateless, so it survives a restart."""
    payload = json.dumps(
        {
            "sub": str(user.id),
            "username": user.username,
            "role": user.role.value,
            "exp": int(time.time()) + SESSION_TTL_SECONDS,
            "nonce": uuid.uuid4().hex,
        },
        separators=(",", ":"),
    ).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"{body}.{_sign(payload)}"


def decode_session_token(token: str) -> dict:
    try:
        body, signature = token.split(".")
    except ValueError as exc:
        raise AuthError("Malformed session token.") from exc
    padded = body + "=" * (-len(body) % 4)
    payload = base64.urlsafe_b64decode(padded.encode())
    if not hmac.compare_digest(_sign(payload), signature):
        raise AuthError("Invalid session token signature.")
    data = json.loads(payload)
    if data.get("exp", 0) < int(time.time()):
        raise AuthError("Session token has expired.")
    return data


async def create_user(
    session: AsyncSession,
    *,
    username: str,
    password: str,
    display_name: str,
    role: UserRole = UserRole.OPERATOR,
    email: str | None = None,
) -> User:
    existing = await session.execute(select(User).where(User.username == username))
    if existing.scalar_one_or_none() is not None:
        raise AuthError(f"Username '{username}' is already taken.")
    user = User(
        username=username,
        display_name=display_name,
        email=email,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
    )
    session.add(user)
    await session.flush()
    await events.audit(
        session,
        action="user.created",
        resource_type="user",
        resource_id=str(user.id),
        outcome="SUCCESS",
        actor_type="SYSTEM",
        detail={"username": username, "role": role.value},
    )
    return user


async def authenticate(
    session: AsyncSession, *, username: str, password: str
) -> tuple[User, str]:
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        # The failure is recorded without revealing which part was wrong.
        await events.security_event(
            session,
            "login_failed",
            f"Failed login attempt for '{username}'.",
            severity="WARNING",
        )
        await events.audit(
            session,
            action="auth.login",
            resource_type="user",
            resource_id=username,
            outcome="DENIED",
            actor_type="HUMAN",
            actor_id=username,
            reason="invalid credentials",
        )
        raise AuthError("Invalid username or password.")

    user.last_login_at = datetime.now(timezone.utc)
    token = create_session_token(user)
    await events.emit(
        session,
        EventCategory.SECURITY,
        "login_succeeded",
        f"{user.username} signed in.",
        source="auth",
        payload={"role": user.role.value},
    )
    await events.audit(
        session,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        outcome="SUCCESS",
        actor_type="HUMAN",
        actor_id=str(user.id),
    )
    return user, token


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await session.get(User, user_id)
