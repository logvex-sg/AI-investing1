"""Agent roster, detail, network graph, research and mutation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecosystem.api.deps import current_user, get_session, require_operator
from ecosystem.api.schemas import ResearchRequest
from ecosystem.db.models.agents import Agent, AgentRelationship
from ecosystem.db.models.enums import AgentStatus
from ecosystem.db.models.identity import User
from ecosystem.db.models.portfolio import Portfolio
from ecosystem.db.models.strategies import Strategy
from ecosystem.services import experiments, memory, portfolio as portfolio_service, strategies
from ecosystem.services.agents import investor, overseer

router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_row(agent: Agent, portfolio: Portfolio | None) -> dict:
    return {
        "id": str(agent.id),
        "codename": agent.codename,
        "display_name": agent.display_name,
        "specialization": agent.specialization,
        "status": agent.status.value,
        "generation_number": agent.generation_number,
        "parents": [
            str(p) for p in (agent.parent_a_id, agent.parent_b_id) if p is not None
        ],
        "time_horizon": agent.time_horizon,
        "asset_preferences": agent.asset_preferences,
        "current_objective": agent.current_objective,
        "portfolio": (
            {
                "id": str(portfolio.id),
                "total_value": str(portfolio.total_value),
                "starting_capital": str(portfolio.starting_capital),
                "cash": str(portfolio.cash),
                "realized_profit": str(portfolio.realized_profit),
                "unrealized_pnl": str(portfolio.unrealized_pnl),
                "max_drawdown": str(portfolio.max_drawdown),
            }
            if portfolio
            else None
        ),
    }


@router.get("")
async def list_agents(
    generation_number: int | None = None,
    include_archived: bool = False,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(Agent).order_by(Agent.generation_number, Agent.codename)
    if generation_number is not None:
        stmt = stmt.where(Agent.generation_number == generation_number)
    if not include_archived:
        stmt = stmt.where(Agent.status == AgentStatus.ACTIVE)
    agents = list((await session.execute(stmt)).scalars().all())

    portfolios = {
        p.agent_id: p
        for p in (
            await session.execute(
                select(Portfolio).where(
                    Portfolio.agent_id.in_([a.id for a in agents] or [uuid.uuid4()])
                )
            )
        ).scalars().all()
    }
    return {"agents": [_agent_row(a, portfolios.get(a.id)) for a in agents]}


@router.get("/network")
async def network(
    generation_number: int | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Nodes and edges for the Network page: the Overseer plus the roster."""
    generation_number = generation_number or await overseer.current_generation_number(session)
    agents = await investor.active_agents(session, generation_number)
    agent_ids = [a.id for a in agents]

    relationships = (
        await session.execute(
            select(AgentRelationship).where(
                AgentRelationship.source_agent_id.in_(agent_ids or [uuid.uuid4()])
            )
        )
    ).scalars().all()

    observation = await overseer.observe(session, generation_number)
    comparison = await overseer.compare_agents(session, generation_number)
    scores = {
        row["codename"]: row["score"] for row in comparison.get("ranking", [])
    }

    nodes: list[dict] = [
        {
            "id": "overseer",
            "kind": "OVERSEER",
            "label": "OVERSEER",
            "specialization": "Coordinator",
            "status": "ACTIVE",
        }
    ]
    for agent in agents:
        nodes.append(
            {
                "id": str(agent.id),
                "kind": "AGENT",
                "label": agent.codename,
                "specialization": agent.specialization,
                "status": agent.status.value,
                "generation": agent.generation_number,
                "score": scores.get(agent.codename, 0.0),
                "objective": agent.current_objective,
            }
        )

    edges: list[dict] = [
        {"source": "overseer", "target": str(a.id), "relation": "COORDINATES", "weight": 1.0}
        for a in agents
    ]
    for rel in relationships:
        edges.append(
            {
                "source": str(rel.source_agent_id),
                "target": str(rel.target_agent_id),
                "relation": rel.relation,
                "weight": float(rel.weight),
                "interactions": rel.interactions,
            }
        )
    return {
        "generation_number": generation_number,
        "nodes": nodes,
        "edges": edges,
        "observation": observation.as_dict(),
        "ranking": comparison.get("ranking", []),
    }


@router.get("/compare")
async def compare(
    generation_number: int | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return await overseer.compare_agents(session, generation_number)


@router.get("/{agent_id}")
async def agent_detail(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")

    portfolio = (
        await session.execute(
            select(Portfolio)
            .options(selectinload(Portfolio.positions))
            .where(Portfolio.agent_id == agent.id)
            .limit(1)
        )
    ).scalar_one_or_none()

    performance = await investor.performance_snapshot(session, agent)
    strategy = (
        await session.get(Strategy, agent.current_strategy_id)
        if agent.current_strategy_id
        else None
    )
    lineage = await _lineage(session, agent)

    detail = _agent_row(agent, portfolio)
    detail.update(
        {
            "personality": agent.personality,
            "risk_profile": agent.risk_profile,
            "inherited_traits": agent.inherited_traits,
            "mutations": agent.mutations,
            "research_priorities": agent.research_priorities,
            "creation_reason": agent.creation_reason,
            "performance": performance,
            "lineage": lineage,
            "strategy": (
                {
                    "id": str(strategy.id),
                    "name": strategy.name,
                    "stage": strategy.stage.value,
                    "current_version": strategy.current_version,
                    "thesis": strategy.thesis,
                }
                if strategy
                else None
            ),
            "memory_counts": await memory.counts_by_kind(session, agent.id),
        }
    )
    return detail


async def _lineage(session: AsyncSession, agent: Agent) -> dict:
    parents = []
    for parent_id in (agent.parent_a_id, agent.parent_b_id):
        if parent_id is None:
            continue
        parent = await session.get(Agent, parent_id)
        if parent is not None:
            parents.append(
                {
                    "id": str(parent.id),
                    "codename": parent.codename,
                    "specialization": parent.specialization,
                    "generation": parent.generation_number,
                }
            )
    children = [
        {
            "id": str(c.id),
            "codename": c.codename,
            "specialization": c.specialization,
            "generation": c.generation_number,
        }
        for c in (
            await session.execute(
                select(Agent).where(
                    (Agent.parent_a_id == agent.id) | (Agent.parent_b_id == agent.id)
                )
            )
        ).scalars().all()
    ]
    return {"parents": parents, "children": children}


@router.get("/{agent_id}/relationships")
async def relationships(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    return await _lineage(session, await _require_agent(session, agent_id))


@router.get("/{agent_id}/experiments")
async def agent_experiments(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    await _require_agent(session, agent_id)
    return {"experiments": await experiments.list_experiments(session, agent_id=agent_id)}


@router.get("/{agent_id}/strategies")
async def agent_strategies(
    agent_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    await _require_agent(session, agent_id)
    rows = (
        await session.execute(
            select(Strategy).where(Strategy.agent_id == agent_id).order_by(Strategy.created_at.desc())
        )
    ).scalars().all()
    return {
        "strategies": [
            {
                "id": str(s.id),
                "name": s.name,
                "stage": s.stage.value,
                "generation_number": s.generation_number,
                "current_version": s.current_version,
                "thesis": s.thesis,
                "asset_universe": s.asset_universe,
            }
            for s in rows
        ]
    }


@router.post("/{agent_id}/research")
async def run_research(
    agent_id: uuid.UUID,
    body: ResearchRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Run one full agent research cycle: recall, propose, validate, learn."""
    agent = await _require_agent(session, agent_id)
    question = await _open_question(session, body.objective)
    result = await investor.run_research_cycle(
        session, agent, question=question, objective=body.objective
    )
    return result


@router.get("/{agent_id}/memory")
async def agent_memory(
    agent_id: uuid.UUID,
    query: str | None = None,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    await _require_agent(session, agent_id)
    if query:
        recalled = await memory.recall(session, agent_id=agent_id, query=query, limit=limit)
        return {"memories": recalled, "counts": await memory.counts_by_kind(session, agent_id)}
    rows = await memory.search(session, agent_id=agent_id, limit=limit)
    return {
        "memories": [
            {
                "id": str(m.id),
                "kind": m.kind.value,
                "title": m.title,
                "content": m.content,
                "importance": float(m.importance),
                "tags": m.tags,
                "generation": m.generation_number,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ],
        "counts": await memory.counts_by_kind(session, agent_id),
    }


async def _require_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


async def _open_question(session: AsyncSession, objective: str):
    """Reuse an open research question, or create one for this objective."""
    from ecosystem.db.models.research import ResearchQuestion

    existing = (
        await session.execute(
            select(ResearchQuestion)
            .where(ResearchQuestion.status == "OPEN")
            .order_by(ResearchQuestion.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    generation_number = await overseer.current_generation_number(session)
    question = ResearchQuestion(
        question=objective,
        rationale="Created from a manual research request.",
        status="OPEN",
        priority=0.6,
        generation_number=generation_number,
        created_by="HUMAN",
    )
    session.add(question)
    await session.flush()
    return question
