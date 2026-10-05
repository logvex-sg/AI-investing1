"""Strategy catalogue, versions, lineage and validation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session, require_operator
from ecosystem.api.schemas import ValidationRequest
from ecosystem.db.models.enums import StrategyStage
from ecosystem.db.models.identity import User
from ecosystem.db.models.strategies import Strategy, StrategyVersion
from ecosystem.services import experiments, strategies

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _strategy_row(strategy: Strategy) -> dict:
    return {
        "id": str(strategy.id),
        "name": strategy.name,
        "slug": strategy.slug,
        "agent_id": str(strategy.agent_id) if strategy.agent_id else None,
        "generation_number": strategy.generation_number,
        "stage": strategy.stage.value,
        "current_version": strategy.current_version,
        "parent_strategy_id": str(strategy.parent_strategy_id)
        if strategy.parent_strategy_id
        else None,
        "asset_universe": strategy.asset_universe,
        "time_horizon": strategy.time_horizon,
        "thesis": strategy.thesis,
        "creation_reason": strategy.creation_reason,
        "retired_reason": strategy.retired_reason,
        "created_at": strategy.created_at.isoformat() if strategy.created_at else None,
    }


@router.get("")
async def list_strategies(
    agent_id: uuid.UUID | None = None,
    generation_number: int | None = None,
    stage: StrategyStage | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(Strategy).order_by(Strategy.created_at.desc())
    if agent_id is not None:
        stmt = stmt.where(Strategy.agent_id == agent_id)
    if generation_number is not None:
        stmt = stmt.where(Strategy.generation_number == generation_number)
    if stage is not None:
        stmt = stmt.where(Strategy.stage == stage)
    rows = list((await session.execute(stmt)).scalars().all())
    return {"strategies": [_strategy_row(s) for s in rows]}


@router.get("/{strategy_id}")
async def strategy_detail(
    strategy_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    strategy = await _require_strategy(session, strategy_id)
    version = await strategies.active_version(session, strategy)
    tree = await strategies.family_tree(session, strategy)
    best = await strategies.best_result(session, strategy.id)
    return {
        "strategy": _strategy_row(strategy),
        "active_version": _version_dict(version),
        "lineage": tree,
        "best_result": (
            {
                "score": float(best.score),
                "passed": best.passed,
                "metrics": best.metrics,
            }
            if best
            else None
        ),
        "experiments": await experiments.list_experiments(
            session, strategy_id=strategy.id, limit=50
        ),
    }


@router.get("/{strategy_id}/versions")
async def strategy_versions(
    strategy_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    await _require_strategy(session, strategy_id)
    versions = await strategies.version_history(session, strategy_id)
    return {"versions": [_version_dict(v) for v in versions]}


@router.get("/{strategy_id}/lineage")
async def strategy_lineage(
    strategy_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    strategy = await _require_strategy(session, strategy_id)
    return await strategies.family_tree(session, strategy)


@router.post("/{strategy_id}/validate")
async def validate_strategy(
    strategy_id: uuid.UUID,
    body: ValidationRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Run backtest, out-of-sample and robustness in sequence.

    The engine computes every number; this endpoint never invents results.
    """
    strategy = await _require_strategy(session, strategy_id)
    version = await strategies.active_version(session, strategy)
    if version is None:
        raise HTTPException(status_code=409, detail="Strategy has no active version.")
    return await experiments.run_full_validation(
        session,
        strategy=strategy,
        version=version,
        agent_id=strategy.agent_id,
        generation_number=strategy.generation_number,
        symbol=body.symbol,
        interval=body.interval,
        limit=body.limit,
    )


@router.post("/{strategy_id}/retire")
async def retire_strategy(
    strategy_id: uuid.UUID,
    reason: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    strategy = await _require_strategy(session, strategy_id)
    await strategies.set_stage(session, strategy, StrategyStage.RETIRED, reason=reason)
    return _strategy_row(strategy)


def _version_dict(version: StrategyVersion | None) -> dict | None:
    if version is None:
        return None
    return {
        "id": str(version.id),
        "version": version.version,
        "parent_version_id": str(version.parent_version_id)
        if version.parent_version_id
        else None,
        "generation_number": version.generation_number,
        "parameters": version.parameters,
        "entry_rules": version.entry_rules,
        "exit_rules": version.exit_rules,
        "risk_rules": version.risk_rules,
        "mutation_reason": version.mutation_reason,
        "creation_reason": version.creation_reason,
        "content_hash": version.content_hash,
        "is_active": version.is_active,
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


async def _require_strategy(session: AsyncSession, strategy_id: uuid.UUID) -> Strategy:
    strategy = await session.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found.")
    return strategy
