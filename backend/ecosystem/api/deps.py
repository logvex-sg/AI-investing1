"""Shared FastAPI dependencies: database sessions and human authentication.

The API is the only place a human identity enters the system. Everything
downstream trusts the `User` resolved here, never a value supplied in a
request body.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.db.models.enums import UserRole
from ecosystem.db.models.identity import User
from ecosystem.db.session import session_scope
from ecosystem.services import auth

_bearer = HTTPBearer(auto_error=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """One transactional session per request."""
    async with session_scope() as session:
        yield session


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        claims = auth.decode_session_token(credentials.credentials)
    except auth.AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    import uuid as _uuid

    user = await auth.get_user(session, _uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User is inactive."
        )
    return user


async def require_operator(user: User = Depends(current_user)) -> User:
    """Guard for actions that change state. Observers may only read."""
    if user.role == UserRole.OBSERVER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has read-only access.",
        )
    return user


async def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required.",
        )
    return user
