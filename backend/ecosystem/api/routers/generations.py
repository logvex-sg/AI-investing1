"""Generations, evolution and the evolutionary record."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session, require_operator
from ecosystem.api.schemas import EvolveRequest
from ecosystem.db.models.agents import Generation
from ecosystem.db.models.identity import User
from ecosystem.services import generation

router = APIRouter(prefix="/generations", tags=["generations"])


def _generation_row(g: Generation) -> dict:
    return {
        "id": str(g.id),
        "number": g.number,
        "label": g.label,
        "status": g.status.value,
        "parent_generation_id": str(g.parent_generation_id)
        if g.parent_generation_id
        else None,
        "started_at": g.started_at.isoformat() if g.started_at else None,
        "completed_at": g.completed_at.isoformat() if g.completed_at else None,
        "creation_reason": g.creation_reason,
        "summary": g.summary,
    }


@router.get("")
async def list_generations(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    rows = (
        await session.execute(select(Generation).order_by(Generation.number))
    ).scalars().all()
    return {"generations": [_generation_row(g) for g in rows]}


@router.get("/current")
async def current(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    try:
        row = await generation.current_generation(session)
    except generation.GenerationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _generation_row(row)


@router.get("/history")
async def history(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Per-generation performance, oldest first. The record is never deleted."""
    return {"history": await generation.performance_history(session)}


@router.get("/{generation_number}")
async def generation_detail(
    generation_number: int,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    row = (
        await session.execute(
            select(Generation).where(Generation.number == generation_number)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Generation not found.")
    return _generation_row(row)


@router.get("/agents/{agent_id}/lineage")
async def agent_lineage(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return await generation.family_tree(session, agent_id)


@router.post("/bootstrap")
async def bootstrap(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Create generation 1 (treasury and the eight founding agents) if absent."""
    return await generation.bootstrap(session)


@router.post("/evolve")
async def evolve(
    body: EvolveRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Archive the current generation and create the next one by selection and
    mutation. Failed agents are preserved as knowledge, never deleted."""
    reason = body.reason or "Manual evolution requested from the control center."
    return await generation.evolve(
        session, reason=reason, max_children=body.limit
    )
