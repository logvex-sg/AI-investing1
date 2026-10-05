"""Generation Manager.

Handles the evolutionary cycle:

    GEN N (8 agents) -> evaluation -> successful characteristics + failure
    knowledge -> combination + mutation -> GEN N+1

Rules enforced here:

* at most `max_active_agents` (8) agents are ACTIVE at any time;
* every child records its parents, inherited traits, mutations and a reason;
* a failed agent is stopped, archived and analysed, never deleted;
* evolutionary history is immutable: past generations and their agents stay in
  the database for inspection and for the Network view.
"""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecosystem.config import get_settings
from ecosystem.db.models.agents import Agent, Generation
from ecosystem.db.models.enums import (
    AgentStatus,
    EventCategory,
    GenerationStatus,
    MemoryKind,
)
from ecosystem.db.models.portfolio import Account, Portfolio
from ecosystem.domain.money import ZERO, money, to_decimal
from ecosystem.services import accounting, events, memory
from ecosystem.services.agents import investor, overseer


class GenerationError(Exception):
    pass


async def create_generation(
    session: AsyncSession,
    *,
    number: int,
    label: str,
    parent_generation_id: uuid.UUID | None,
    creation_reason: str,
    status: GenerationStatus = GenerationStatus.PLANNED,
) -> Generation:
    generation = Generation(
        number=number,
        label=label,
        status=status,
        started_at=datetime.now(timezone.utc),
        parent_generation_id=parent_generation_id,
        creation_reason=creation_reason,
        summary={},
    )
    session.add(generation)
    await session.flush()
    await events.emit(
        session,
        EventCategory.SYSTEM,
        "generation_created",
        f"Generation {number} ({label}) created: {creation_reason}",
        source="generation",
        payload={"generation_id": str(generation.id), "number": number},
    )
    return generation


async def fund_agents(
    session: AsyncSession,
    agents: list[Agent],
    *,
    total: object | None = None,
) -> dict[str, str]:
    """Open a simulated account and portfolio for each agent.

    Capital comes from the System Treasury's trading allocation. Each agent
    gets an equal share; the treasury's protected capital is never touched.
    """
    settings = get_settings()
    treasury = await accounting.ensure_treasury(session)
    trading_account = treasury["trading"]

    if not agents:
        return {}

    pool = money(
        to_decimal(total) if total is not None else to_decimal(trading_account.cash)
    )
    per_agent = money(pool / len(agents))
    if per_agent <= 0:
        raise GenerationError("treasury trading allocation is empty")

    accounts: dict[str, str] = {}
    # Rounding `pool / n` leaves a remainder of up to n-1 minor units. Give it
    # to the last agent so the treasury is never asked for more than it holds.
    for index, agent in enumerate(agents):
        share = per_agent
        if index == len(agents) - 1:
            remainder = money(pool - per_agent * (len(agents) - 1))
            if remainder > 0:
                share = remainder
        # Open the account empty, then move capital in from the treasury. That
        # way the transfer is the single recorded event and the reconstructed
        # ledger matches the stored balance.
        agent_account, portfolio = await accounting.create_agent_account(
            session, agent, ZERO
        )
        await accounting.transfer(
            session,
            source_account_id=trading_account.id,
            destination_account_id=agent_account.id,
            amount=share,
            memo=f"Funding for {agent.codename} (generation {agent.generation_number}).",
            destination_is_opening=True,
        )
        portfolio.starting_capital = share
        portfolio.peak_value = money(portfolio.total_value)
        accounts[agent.codename] = str(portfolio.id)
    await session.flush()
    return accounts


async def bootstrap(session: AsyncSession) -> dict:
    """Create generation 1: the treasury, the eight founding agents, and the
    first Overseer plan. Idempotent: running it twice is a no-op."""
    existing = await session.execute(select(func.max(Generation.number)))
    if existing.scalar() is not None:
        generation = await current_generation(session)
        roster = await investor.active_agents(session, generation.number)
        return {
            "status": "ALREADY_BOOTSTRAPPED",
            "generation": generation.number,
            "agents": [a.codename for a in roster],
        }

    settings = get_settings()
    treasury = await accounting.ensure_treasury(session)
    generation = await create_generation(
        session,
        number=settings.initial_generation,
        label=f"GEN {settings.initial_generation}",
        parent_generation_id=None,
        creation_reason="Genesis generation: the eight founding specializations.",
        status=GenerationStatus.ACTIVE,
    )
    agents = await investor.create_initial_agents(session, generation)
    portfolios = await fund_agents(session, agents)
    for agent in agents:
        agent.current_objective = (
            "Establish whether my specialization has a measurable, cost-adjusted edge."
        )
    generation.summary = {
        "agents": [a.codename for a in agents],
        "specializations": [a.specialization for a in agents],
        "treasury": {k: str(v.cash) for k, v in treasury.items()},
    }
    await session.flush()

    await events.emit(
        session,
        EventCategory.SYSTEM,
        "generation_started",
        f"Generation {generation.number} started with {len(agents)} agents.",
        source="generation",
        payload={"generation_id": str(generation.id), "portfolios": portfolios},
    )
    return {
        "status": "BOOTSTRAPPED",
        "generation": generation.number,
        "agents": [a.codename for a in agents],
        "portfolios": portfolios,
    }


async def current_generation(session: AsyncSession) -> Generation:
    result = await session.execute(select(Generation).order_by(Generation.number.desc()).limit(1))
    generation = result.scalar_one_or_none()
    if generation is None:
        raise GenerationError("no generation exists; bootstrap first")
    return generation


async def archive_generation(
    session: AsyncSession, generation: Generation, *, reclaim_capital: bool = True
) -> int:
    """Archive a generation, snapshot its performance, and stop its agents.

    Nothing is deleted. If `reclaim_capital` is set, each agent's positions are
    liquidated at the last mark and the proceeds are returned to the System
    Treasury's trading allocation, so the next generation can be funded without
    minting new money.
    """
    generation.status = GenerationStatus.ARCHIVED
    generation.completed_at = datetime.now(timezone.utc)
    result = await session.execute(
        select(Agent).where(
            Agent.generation_id == generation.id, Agent.status == AgentStatus.ACTIVE
        )
    )
    agents = list(result.scalars().all())

    performance: dict[str, dict] = {}
    total_value = ZERO
    starting = ZERO
    stopped = 0
    for agent in agents:
        snapshot = await investor.performance_snapshot(session, agent)
        portfolio = (
            await session.execute(
                select(Portfolio).where(Portfolio.agent_id == agent.id)
            )
        ).scalar_one_or_none()
        value = money(portfolio.total_value) if portfolio else ZERO
        capital = money(portfolio.starting_capital) if portfolio else ZERO
        total_value = money(total_value + value)
        starting = money(starting + capital)
        performance[agent.codename] = {
            "score": snapshot.get("score", 0.0),
            "experiments": snapshot.get("experiments", 0),
            "portfolio_value": str(value),
            "starting_capital": str(capital),
        }
        agent.status = AgentStatus.ARCHIVED
        stopped += 1
        await memory.remember(
            session,
            agent_id=agent.id,
            kind=MemoryKind.LESSON,
            title="Agent archived",
            content=(
                f"I was archived at the end of generation {generation.number} with a "
                f"final portfolio value of {value}. My strategies and lessons remain "
                f"available to my descendants."
            ),
            importance=0.5,
            tags=["archive", "generation"],
            generation_number=generation.number,
        )

    generation.summary = {
        **(generation.summary or {}),
        "performance": {
            "agents": performance,
            "portfolio_value": str(total_value),
            "starting_capital": str(starting),
        },
    }

    if reclaim_capital:
        await _reclaim_capital(session, agents)

    await events.emit(
        session,
        EventCategory.SYSTEM,
        "generation_archived",
        f"Generation {generation.number} archived; {stopped} agents stopped and preserved.",
        source="generation",
        payload={"generation_id": str(generation.id), "stopped": stopped},
    )
    return stopped


async def _reclaim_capital(session: AsyncSession, agents: list[Agent]) -> Decimal:
    """Liquidate archived agents' positions and return cash to the treasury."""
    treasury = await accounting.ensure_treasury(session)
    trading_account = treasury["trading"]
    reclaimed = ZERO

    for agent in agents:
        portfolio = (
            await session.execute(
                select(Portfolio)
                .options(selectinload(Portfolio.positions))
                .where(Portfolio.agent_id == agent.id)
            )
        ).scalar_one_or_none()
        if portfolio is None:
            continue
        for position in list(portfolio.positions):
            if position.quantity <= 0:
                continue
            price = money(position.last_price or position.average_cost)
            await accounting.apply_sell(
                session,
                portfolio,
                symbol=position.symbol,
                qty=position.quantity,
                price=price,
                fee=ZERO,
                memo=f"Generation liquidation for {agent.codename}.",
            )
        cash = money(portfolio.cash)
        if cash > 0:
            await accounting.transfer(
                session,
                source_account_id=portfolio.account_id,
                destination_account_id=trading_account.id,
                amount=cash,
                memo=f"Recycled capital from {agent.codename}.",
            )
            reclaimed = money(reclaimed + cash)
    await session.flush()
    return reclaimed


async def evolve(
    session: AsyncSession,
    *,
    reason: str,
    max_children: int | None = None,
) -> dict:
    """Create the next generation by evaluating, selecting and mutating.

    Returns a report describing the parents, children and mutations.
    """
    settings = get_settings()
    limit = max_children or settings.max_active_agents
    parent_generation = await current_generation(session)

    evaluation = await overseer.evaluate_generation(session, parent_generation.number)
    characteristics = await overseer.identify_useful_characteristics(
        session, parent_generation.number
    )
    await overseer.register_relationships(session, parent_generation.number)

    parents = await investor.active_agents(session, parent_generation.number)
    if not parents:
        raise GenerationError("no active agents to evolve from")

    ranking = evaluation["ranking"]
    ranked_agents = {a.codename: a for a in parents}
    ordered = [ranked_agents[r["codename"]] for r in ranking if r["codename"] in ranked_agents]
    # The strongest agents are the most likely parents, but every agent that
    # produced evidence can contribute; diversity is preserved deliberately.
    breeding_pool = ordered[: max(2, len(ordered) // 2)]

    # Close out the parent generation first. This snapshots its performance and
    # returns its capital to the treasury, so the children can be funded from
    # the same pool rather than from newly minted money.
    await archive_generation(session, parent_generation)

    next_number = parent_generation.number + 1
    child_generation = await create_generation(
        session,
        number=next_number,
        label=f"GEN {next_number}",
        parent_generation_id=parent_generation.id,
        creation_reason=reason,
        status=GenerationStatus.ACTIVE,
    )

    children: list[Agent] = []
    mutation_log: list[dict] = []
    for index in range(min(limit, settings.max_active_agents)):
        rng = random.Random(f"{next_number}:{index}:{settings.secret_key[:8]}")
        # Deterministic selection: cycle through the breeding pool so lineage
        # is balanced rather than a monoculture of the single best agent.
        parent_a = breeding_pool[index % len(breeding_pool)]
        parent_b = (
            breeding_pool[(index + 1) % len(breeding_pool)]
            if len(breeding_pool) > 1
            else None
        )
        child, mutations = await _spawn_child(
            session,
            generation=child_generation,
            index=index,
            parent_a=parent_a,
            parent_b=parent_b,
            rng=rng,
            characteristics=characteristics,
        )
        children.append(child)
        mutation_log.append({"codename": child.codename, "mutations": mutations})

    await fund_agents(session, children)

    # Carry distilled knowledge, then archive the parents.
    for child in children:
        if child.parent_a_id:
            await memory.carry_forward(
                session,
                from_agent_id=child.parent_a_id,
                to_agent_id=child.id,
                kinds=[MemoryKind.LESSON, MemoryKind.DISCOVERY, MemoryKind.FAILURE],
                limit=10,
            )
        if child.parent_b_id:
            await memory.carry_forward(
                session,
                from_agent_id=child.parent_b_id,
                to_agent_id=child.id,
                kinds=[MemoryKind.LESSON, MemoryKind.DISCOVERY],
                limit=6,
            )

    child_generation.summary = {
        "parents": [a.codename for a in breeding_pool],
        "children": [a.codename for a in children],
        "characteristics": characteristics["characteristics"],
        "mutations": mutation_log,
    }
    await session.flush()

    await events.emit(
        session,
        EventCategory.SYSTEM,
        "generation_evolved",
        f"Generation {next_number} created from {len(breeding_pool)} parents "
        f"with {len(children)} children.",
        source="generation",
        payload={
            "generation_id": str(child_generation.id),
            "parents": [a.codename for a in breeding_pool],
            "children": [a.codename for a in children],
        },
    )
    return {
        "generation": next_number,
        "parents": [
            {"codename": a.codename, "specialization": a.specialization}
            for a in breeding_pool
        ],
        "children": [
            {"codename": a.codename, "specialization": a.specialization} for a in children
        ],
        "characteristics": characteristics["characteristics"],
        "mutations": mutation_log,
        "evaluation": evaluation,
    }


async def _spawn_child(
    session: AsyncSession,
    *,
    generation: Generation,
    index: int,
    parent_a: Agent,
    parent_b: Agent | None,
    rng: random.Random,
    characteristics: dict,
) -> tuple[Agent, list[dict]]:
    """Combine parents and apply bounded mutations to produce a child."""
    inherited = _combine(parent_a, parent_b, rng)
    mutations = _mutate(inherited, rng)

    # Specialization: usually the primary parent's, occasionally the other
    # parent's, so a lineage can shift strategy focus over time.
    specialization = parent_a.specialization
    if parent_b is not None and rng.random() < 0.25:
        specialization = parent_b.specialization

    mutation_summary = ", ".join(f"{m['trait']} {m['delta']:+.2f}" for m in mutations) or "none"
    child = Agent(
        codename=investor.codename_for(generation.number, index),
        display_name=f"{specialization.capitalize()} Agent",
        specialization=specialization,
        status=AgentStatus.ACTIVE,
        generation_id=generation.id,
        generation_number=generation.number,
        parent_a_id=parent_a.id,
        parent_b_id=parent_b.id if parent_b else None,
        personality=inherited["personality"],
        risk_profile=inherited["risk_profile"],
        inherited_traits=inherited,
        mutations=mutations,
        asset_preferences=sorted(
            set(parent_a.asset_preferences)
            | (set(parent_b.asset_preferences) if parent_b else set())
        ),
        time_horizon=parent_a.time_horizon,
        research_priorities=list(parent_a.research_priorities)[-5:],
        creation_reason=(
            f"Child of {parent_a.codename}"
            + (f" and {parent_b.codename}" if parent_b else "")
            + f". Inherited from generation {parent_a.generation_number}. "
            + f"Mutations: {mutation_summary}. "
            + (characteristics["characteristics"][0] if characteristics["characteristics"] else "")
        ),
        current_objective="Test whether my inherited edge survives with my mutations.",
    )
    session.add(child)
    await session.flush()

    await memory.remember(
        session,
        agent_id=child.id,
        kind=MemoryKind.RELATIONSHIP,
        title="Lineage",
        content=(
            f"I descend from {parent_a.codename}"
            + (f" and {parent_b.codename}" if parent_b else "")
            + f". Mutations applied: {mutation_summary}."
        ),
        importance=0.6,
        tags=["lineage", "inheritance"],
        generation_number=generation.number,
    )
    return child, mutations


def _combine(parent_a: Agent, parent_b: Agent | None, rng: random.Random) -> dict:
    """Blend two parents' traits. With one parent, traits pass through."""
    if parent_b is None:
        return {
            "personality": dict(parent_a.personality),
            "risk_profile": dict(parent_a.risk_profile),
            "from": [parent_a.codename],
        }

    def blend(key: str) -> dict:
        out = {}
        keys = set(parent_a.personality.get(key, {})) | set(parent_b.personality.get(key, {}))
        for trait in keys:
            a = float(parent_a.personality.get(key, {}).get(trait, 0.5))
            b = float(parent_b.personality.get(key, {}).get(trait, 0.5))
            out[trait] = round(a * rng.uniform(0.3, 0.7) + b * rng.uniform(0.3, 0.7), 4)
        return out

    return {
        "personality": blend("personality"),
        "risk_profile": blend("risk_profile"),
        "from": [parent_a.codename, parent_b.codename],
    }


def _mutate(inherited: dict, rng: random.Random) -> list[dict]:
    """Apply a small number of bounded mutations to inherited traits."""
    mutations: list[dict] = []
    for key in ("personality", "risk_profile"):
        traits = inherited.get(key, {})
        for trait in list(traits):
            if rng.random() < 0.35:
                delta = round(rng.uniform(-0.15, 0.15), 4)
                traits[trait] = round(max(0.0, min(1.0, float(traits[trait]) + delta)), 4)
                mutations.append({"trait": f"{key}.{trait}", "delta": delta})
    return mutations


async def family_tree(session: AsyncSession, agent_id: uuid.UUID) -> dict:
    """Ancestry and descendants of an agent, for the Relationships tab."""
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise GenerationError("agent not found")

    ancestors: list[dict] = []
    queue = [agent.parent_a_id, agent.parent_b_id]
    seen: set[uuid.UUID] = set()
    while queue:
        current_id = queue.pop(0)
        if current_id is None or current_id in seen:
            continue
        seen.add(current_id)
        parent = await session.get(Agent, current_id)
        if parent is None:
            continue
        ancestors.append(
            {
                "id": str(parent.id),
                "codename": parent.codename,
                "specialization": parent.specialization,
                "generation": parent.generation_number,
            }
        )
        queue.extend([parent.parent_a_id, parent.parent_b_id])

    children_result = await session.execute(
        select(Agent).where(
            (Agent.parent_a_id == agent_id) | (Agent.parent_b_id == agent_id)
        )
    )
    children = [
        {
            "id": str(c.id),
            "codename": c.codename,
            "specialization": c.specialization,
            "generation": c.generation_number,
            "mutations": c.mutations,
        }
        for c in children_result.scalars().all()
    ]

    return {
        "agent": {
            "id": str(agent.id),
            "codename": agent.codename,
            "generation": agent.generation_number,
            "specialization": agent.specialization,
        },
        "ancestors": ancestors,
        "children": children,
        "inherited_traits": agent.inherited_traits,
        "mutations": agent.mutations,
        "creation_reason": agent.creation_reason,
    }


async def performance_history(session: AsyncSession) -> list[dict]:
    """Per-generation performance summary, oldest first."""
    result = await session.execute(select(Generation).order_by(Generation.number))
    generations = list(result.scalars().all())

    history: list[dict] = []
    for generation in generations:
        agents_result = await session.execute(
            select(Agent).where(Agent.generation_id == generation.id)
        )
        agents = list(agents_result.scalars().all())
        recorded = (generation.summary or {}).get("performance") or {}
        if recorded:
            # An archived generation is liquidated back into the treasury, so its
            # live portfolio rows read zero. The snapshot captured at archive
            # time is the truthful figure.
            recorded_agents = recorded.get("agents", {})
            scores = [float(v.get("score", 0.0)) for v in recorded_agents.values()]
            total_value = to_decimal(recorded.get("portfolio_value", ZERO))
            starting = to_decimal(recorded.get("starting_capital", ZERO))
        else:
            scores = []
            for agent in agents:
                snapshot = await investor.performance_snapshot(session, agent)
                scores.append(snapshot.get("score", 0.0))
            portfolios_result = await session.execute(
                select(Portfolio)
                .options(selectinload(Portfolio.positions))
                .where(Portfolio.generation_number == generation.number)
            )
            portfolios = list(portfolios_result.scalars().all())
            total_value = sum((p.total_value for p in portfolios), ZERO)
            starting = sum((p.starting_capital for p in portfolios), ZERO)

        history.append(
            {
                "number": generation.number,
                "label": generation.label,
                "status": generation.status.value,
                "agents": [a.codename for a in agents],
                "best_score": round(max(scores), 6) if scores else 0.0,
                "average_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
                "portfolio_value": str(money(total_value)),
                "starting_capital": str(money(starting)),
                "return": str(money((total_value - starting) / starting))
                if starting > 0
                else "0",
                "creation_reason": generation.creation_reason,
                "started_at": generation.started_at.isoformat()
                if generation.started_at
                else None,
                "completed_at": generation.completed_at.isoformat()
                if generation.completed_at
                else None,
            }
        )
    return history
