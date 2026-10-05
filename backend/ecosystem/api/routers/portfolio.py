"""Portfolios, positions, treasury and accounting reconciliation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session
from ecosystem.db.models.identity import User
from ecosystem.db.models.portfolio import Account, Portfolio
from ecosystem.domain.money import ZERO, money
from ecosystem.services import portfolio

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


@router.get("")
async def list_portfolios(
    generation_number: int | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(Portfolio).order_by(Portfolio.created_at)
    if generation_number is not None:
        stmt = stmt.where(Portfolio.generation_number == generation_number)
    rows = list((await session.execute(stmt)).scalars().all())
    return {"portfolios": [portfolio._portfolio_summary(p) for p in rows]}


@router.get("/treasury")
async def treasury(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    rows = (
        await session.execute(
            select(Account).where(Account.is_system.is_(True)).order_by(Account.kind)
        )
    ).scalars().all()
    return {
        "accounts": [
            {
                "id": str(a.id),
                "name": a.name,
                "kind": a.kind.value,
                "currency": a.currency,
                "starting_capital": str(a.starting_capital),
                "cash": str(a.cash),
                "reserved_capital": str(a.reserved_capital),
                "is_locked": a.is_locked,
            }
            for a in rows
        ],
        "total": str(sum((money(a.cash) for a in rows), ZERO)),
    }


@router.get("/contributions")
async def contributions(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return {"contributions": await portfolio.agent_contributions(session)}


@router.get("/reconciliation")
async def reconciliation(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return await portfolio.accounting_reconciliation(session)


@router.get("/{portfolio_id}")
async def portfolio_detail(
    portfolio_id: uuid.UUID,
    interval: str = "1d",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    try:
        return await portfolio.portfolio_detail(session, portfolio_id, interval)
    except Exception as exc:  # noqa: BLE001 - map a missing row to 404
        raise HTTPException(status_code=404, detail=str(exc)) from exc
