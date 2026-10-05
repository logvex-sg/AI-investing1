"""Human authentication. The only source of a `User` identity."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session, require_admin
from ecosystem.api.schemas import LoginRequest, RegisterRequest
from ecosystem.db.models.identity import User
from ecosystem.services import auth

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_row(user: User) -> dict:
    return {
        "id": str(user.id),
        "username": user.username,
        "display_name": user.display_name,
        "email": user.email,
        "role": user.role.value,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.post("/login")
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        user, token = await auth.authenticate(
            session, username=body.username, password=body.password
        )
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"token": token, "user": _user_row(user)}


@router.get("/me")
async def me(user: User = Depends(current_user)) -> dict:
    return _user_row(user)


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> dict:
    rows = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    return {"users": [_user_row(u) for u in rows]}


@router.post("/users")
async def create_user(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_admin),
) -> dict:
    try:
        created = await auth.create_user(
            session,
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            role=body.role,
            email=body.email,
        )
    except auth.AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _user_row(created)


@router.post("/bootstrap")
async def bootstrap_admin(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Create the very first administrator.

    This is only possible while no user exists, so it cannot be used to
    escalate once the system is live.
    """
    existing = (
        await session.execute(select(func.count()).select_from(User))
    ).scalar_one()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Bootstrap is disabled: a user already exists.",
        )
    from ecosystem.db.models.enums import UserRole

    created = await auth.create_user(
        session,
        username=body.username,
        password=body.password,
        display_name=body.display_name,
        role=UserRole.ADMIN,
        email=body.email,
    )
    return _user_row(created)
