"""Strategy lifecycle service.

A strategy is a lineage of immutable versions. Every mutation records its
parent version and a reason, so the family tree of ideas is never lost.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.db.models.enums import EventCategory, MemoryKind, StrategyStage
from ecosystem.db.models.research import Experiment, ExperimentResult
from ecosystem.db.models.strategies import Strategy, StrategyVersion
from ecosystem.domain.strategy_signals import describe_strategy
from ecosystem.services import events, memory


class StrategyError(Exception):
    pass


def _content_hash(parameters: dict, entry_rules: list, exit_rules: list, risk_rules: dict) -> str:
    payload = json.dumps(
        {
            "parameters": parameters,
            "entry_rules": entry_rules,
            "exit_rules": exit_rules,
            "risk_rules": risk_rules,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _slug(name: str) -> str:
    base = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    return f"{base[:110]}-{uuid.uuid4().hex[:8]}"


async def create_strategy(
    session: AsyncSession,
    *,
    name: str,
    agent_id: uuid.UUID | None,
    generation_number: int,
    thesis: str,
    creation_reason: str,
    asset_universe: list[str],
    time_horizon: str,
    parameters: dict,
    entry_rules: list[dict],
    exit_rules: list[dict],
    risk_rules: dict | None = None,
) -> tuple[Strategy, StrategyVersion]:
    """Create a strategy in the IDEA stage with its first version."""
    strategy = Strategy(
        name=name,
        slug=_slug(name),
        agent_id=agent_id,
        generation_number=generation_number,
        stage=StrategyStage.IDEA,
        current_version=1,
        asset_universe=asset_universe,
        time_horizon=time_horizon,
        thesis=thesis,
        creation_reason=creation_reason,
    )
    session.add(strategy)
    await session.flush()

    version = StrategyVersion(
        strategy_id=strategy.id,
        version=1,
        agent_id=agent_id,
        generation_number=generation_number,
        parameters=parameters,
        entry_rules=entry_rules,
        exit_rules=exit_rules,
        risk_rules=risk_rules or {},
        creation_reason=creation_reason,
        content_hash=_content_hash(parameters, entry_rules, exit_rules, risk_rules or {}),
        is_active=True,
    )
    session.add(version)
    await session.flush()

    await events.emit(
        session,
        EventCategory.RESEARCH,
        "strategy_created",
        f"Strategy '{name}' created for generation {generation_number}.",
        source="strategies",
        agent_id=agent_id,
        payload={"strategy_id": str(strategy.id), "version_id": str(version.id)},
    )
    if agent_id is not None:
        await memory.remember(
            session,
            agent_id=agent_id,
            kind=MemoryKind.STRATEGY_VERSION,
            title=f"Created strategy {name}",
            content=f"{describe_strategy(parameters, entry_rules, exit_rules)} Thesis: {thesis}",
            importance=0.6,
            tags=["strategy", "creation"],
            source_type="strategy_version",
            source_id=version.id,
            generation_number=generation_number,
        )
    return strategy, version


async def mutate_strategy(
    session: AsyncSession,
    *,
    strategy: Strategy,
    parent_version: StrategyVersion,
    parameters: dict,
    entry_rules: list[dict],
    exit_rules: list[dict],
    mutation_reason: str,
    risk_rules: dict | None = None,
) -> StrategyVersion:
    """Derive a new version from a parent, recording why the change was made.

    The new number is one past the strategy's highest existing version, not
    simply the parent's number plus one: several probes can branch from the
    same parent.
    """
    result = await session.execute(
        select(func.max(StrategyVersion.version)).where(
            StrategyVersion.strategy_id == strategy.id
        )
    )
    highest = result.scalar() or parent_version.version
    new_version_number = max(highest, strategy.current_version) + 1
    version = StrategyVersion(
        strategy_id=strategy.id,
        version=new_version_number,
        parent_version_id=parent_version.id,
        agent_id=strategy.agent_id,
        generation_number=strategy.generation_number,
        parameters=parameters,
        entry_rules=entry_rules,
        exit_rules=exit_rules,
        risk_rules=risk_rules if risk_rules is not None else parent_version.risk_rules,
        mutation_reason=mutation_reason,
        creation_reason=f"Mutation of v{parent_version.version}: {mutation_reason}",
        content_hash=_content_hash(
            parameters, entry_rules, exit_rules,
            risk_rules if risk_rules is not None else parent_version.risk_rules,
        ),
        is_active=True,
    )
    session.add(version)
    await session.flush()

    # Supersede the parent but keep it for the historical record.
    parent_version.is_active = False
    strategy.current_version = new_version_number

    await events.emit(
        session,
        EventCategory.RESEARCH,
        "strategy_mutated",
        f"Strategy '{strategy.name}' v{parent_version.version} -> v{new_version_number}: "
        f"{mutation_reason}",
        source="strategies",
        agent_id=strategy.agent_id,
        payload={
            "strategy_id": str(strategy.id),
            "parent_version_id": str(parent_version.id),
            "version_id": str(version.id),
        },
    )
    if strategy.agent_id is not None:
        await memory.remember(
            session,
            agent_id=strategy.agent_id,
            kind=MemoryKind.STRATEGY_VERSION,
            title=f"Mutated {strategy.name} to v{new_version_number}",
            content=mutation_reason,
            importance=0.55,
            tags=["strategy", "mutation"],
            source_type="strategy_version",
            source_id=version.id,
            generation_number=strategy.generation_number,
        )
    return version


async def set_stage(
    session: AsyncSession, strategy: Strategy, stage: StrategyStage, reason: str | None = None
) -> Strategy:
    """Advance (or retire) a strategy. Stage transitions are recorded."""
    previous = strategy.stage
    strategy.stage = stage
    if stage == StrategyStage.RETIRED and reason:
        strategy.retired_reason = reason
    await events.emit(
        session,
        EventCategory.RESEARCH,
        "strategy_stage_changed",
        f"Strategy '{strategy.name}' moved {previous.value} -> {stage.value}.",
        source="strategies",
        agent_id=strategy.agent_id,
        payload={"strategy_id": str(strategy.id), "stage": stage.value},
    )
    return strategy


async def active_version(session: AsyncSession, strategy: Strategy) -> StrategyVersion | None:
    result = await session.execute(
        select(StrategyVersion)
        .where(
            StrategyVersion.strategy_id == strategy.id,
            StrategyVersion.version == strategy.current_version,
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def version_history(session: AsyncSession, strategy_id: uuid.UUID) -> list[StrategyVersion]:
    result = await session.execute(
        select(StrategyVersion)
        .where(StrategyVersion.strategy_id == strategy_id)
        .order_by(StrategyVersion.version)
    )
    return list(result.scalars().all())


async def family_tree(session: AsyncSession, strategy: Strategy) -> dict:
    """Return the strategy's ancestry and descendants for the UI."""
    versions = await version_history(session, strategy.id)
    ancestors: list[dict] = []
    current = strategy
    seen: set[uuid.UUID] = set()
    while current.parent_strategy_id and current.parent_strategy_id not in seen:
        seen.add(current.parent_strategy_id)
        parent = await session.get(Strategy, current.parent_strategy_id)
        if parent is None:
            break
        ancestors.append(
            {"id": str(parent.id), "name": parent.name, "generation": parent.generation_number}
        )
        current = parent

    children_result = await session.execute(
        select(Strategy).where(Strategy.parent_strategy_id == strategy.id)
    )
    children = [
        {"id": str(c.id), "name": c.name, "generation": c.generation_number}
        for c in children_result.scalars().all()
    ]
    return {
        "strategy": {"id": str(strategy.id), "name": strategy.name},
        "ancestors": list(reversed(ancestors)),
        "children": children,
        "versions": [
            {
                "id": str(v.id),
                "version": v.version,
                "parent_version_id": str(v.parent_version_id) if v.parent_version_id else None,
                "mutation_reason": v.mutation_reason,
                "is_active": v.is_active,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
    }


async def best_result(
    session: AsyncSession, strategy_id: uuid.UUID
) -> ExperimentResult | None:
    """Highest-scoring result recorded for a strategy, across all experiments."""
    result = await session.execute(
        select(ExperimentResult)
        .join(Experiment, Experiment.id == ExperimentResult.experiment_id)
        .where(Experiment.strategy_id == strategy_id)
        .order_by(ExperimentResult.score.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()
