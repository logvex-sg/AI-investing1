"""Investor agents.

An agent is a logical identity, not a model instance. It has a specialization,
a personality, a risk profile and persistent memory, and it borrows the shared
model only while it is being served by the scheduler.

The agent's job is to propose; the engine's job is to measure. Nothing an
agent produces is trusted until the deterministic pipeline has graded it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.config import get_settings
from ecosystem.db.models.agents import Agent, AgentRelationship, Generation
from ecosystem.db.models.enums import (
    AgentStatus,
    EventCategory,
    MemoryKind,
    StrategyStage,
)
from ecosystem.db.models.research import Hypothesis, ResearchQuestion
from ecosystem.domain.evaluation import evaluate_agent
from ecosystem.domain.market import annualized_volatility, classify_regime
from ecosystem.domain.strategy_signals import conditions_met
from ecosystem.services import events, market_data, memory, strategies
from ecosystem.services.agents import prompts
from ecosystem.services.llm import LLMRequest, StrategyProposal, get_scheduler

# The eight founding specializations, in roster order.
SPECIALIZATIONS: list[dict[str, Any]] = [
    {
        "specialization": "momentum",
        "display_name": "Momentum",
        "assets": ["BTCUSD", "SPX"],
        "horizon": "short",
        "personality": {"aggression": 0.8, "patience": 0.3, "curiosity": 0.6},
        "risk_profile": {"appetite": 0.75, "max_allocation": 0.25},
    },
    {
        "specialization": "quantitative",
        "display_name": "Quantitative",
        "assets": ["BTCUSD", "EURUSD"],
        "horizon": "short",
        "personality": {"aggression": 0.5, "patience": 0.7, "curiosity": 0.8},
        "risk_profile": {"appetite": 0.5, "max_allocation": 0.2},
    },
    {
        "specialization": "value",
        "display_name": "Value",
        "assets": ["SPX", "BTCUSD"],
        "horizon": "long",
        "personality": {"aggression": 0.4, "patience": 0.9, "curiosity": 0.5},
        "risk_profile": {"appetite": 0.45, "max_allocation": 0.2},
    },
    {
        "specialization": "defensive",
        "display_name": "Defensive",
        "assets": ["USD", "EUR"],
        "horizon": "medium",
        "personality": {"aggression": 0.2, "patience": 0.85, "curiosity": 0.4},
        "risk_profile": {"appetite": 0.25, "max_allocation": 0.1},
    },
    {
        "specialization": "macro",
        "display_name": "Macro",
        "assets": ["EURUSD", "SPX"],
        "horizon": "long",
        "personality": {"aggression": 0.5, "patience": 0.8, "curiosity": 0.7},
        "risk_profile": {"appetite": 0.5, "max_allocation": 0.2},
    },
    {
        "specialization": "volatility",
        "display_name": "Volatility",
        "assets": ["BTCUSD"],
        "horizon": "short",
        "personality": {"aggression": 0.9, "patience": 0.2, "curiosity": 0.85},
        "risk_profile": {"appetite": 0.7, "max_allocation": 0.15},
    },
    {
        "specialization": "experimental",
        "display_name": "Experimental",
        "assets": ["BTCUSD", "EURUSD", "SPX"],
        "horizon": "short",
        "personality": {"aggression": 0.7, "patience": 0.4, "curiosity": 1.0},
        "risk_profile": {"appetite": 0.6, "max_allocation": 0.15},
    },
    {
        "specialization": "diversified",
        "display_name": "Diversified",
        "assets": ["BTCUSD", "EURUSD", "SPX", "USD"],
        "horizon": "medium",
        "personality": {"aggression": 0.45, "patience": 0.6, "curiosity": 0.6},
        "risk_profile": {"appetite": 0.45, "max_allocation": 0.2},
    },
]

GENERATION_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def codename_for(generation_number: int, index: int) -> str:
    """A1..A8 for generation 1, B1..B8 for generation 2, and so on."""
    letter = GENERATION_LETTERS[(generation_number - 1) % len(GENERATION_LETTERS)]
    return f"{letter}{index + 1}"


async def create_initial_agents(
    session: AsyncSession, generation: Generation
) -> list[Agent]:
    """Found generation 1's roster: the eight specializations in order."""
    agents: list[Agent] = []
    for index, spec in enumerate(SPECIALIZATIONS[: get_settings().max_active_agents]):
        agent = Agent(
            codename=codename_for(generation.number, index),
            display_name=f"{spec['display_name']} Agent",
            specialization=spec["specialization"],
            status=AgentStatus.ACTIVE,
            generation_id=generation.id,
            generation_number=generation.number,
            personality=spec["personality"],
            risk_profile=spec["risk_profile"],
            inherited_traits={},
            mutations=[],
            asset_preferences=spec["assets"],
            time_horizon=spec["horizon"],
            research_priorities=[],
            creation_reason=f"Founding agent for generation {generation.number}.",
            current_objective="Awaiting the Overseer's first assignment.",
        )
        session.add(agent)
        await session.flush()
        await memory.remember(
            session,
            agent_id=agent.id,
            kind=MemoryKind.OBSERVATION,
            title="Agent founded",
            content=(
                f"I am {agent.codename}, a {agent.specialization} investor agent in "
                f"generation {generation.number}. I will propose falsifiable "
                f"strategies and let the engine judge them."
            ),
            importance=0.4,
            tags=["identity", "genesis"],
            generation_number=generation.number,
        )
        agents.append(agent)
    return agents


async def active_agents(session: AsyncSession, generation_number: int | None = None) -> list[Agent]:
    stmt = select(Agent).where(Agent.status == AgentStatus.ACTIVE)
    if generation_number is not None:
        stmt = stmt.where(Agent.generation_number == generation_number)
    stmt = stmt.order_by(Agent.codename)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def _market_context(session: AsyncSession, agent: Agent) -> tuple[str, str]:
    symbol = agent.asset_preferences[0] if agent.asset_preferences else "BTCUSD"
    try:
        _, series = await market_data.get_or_load_series(session, symbol, "1d", 180)
    except Exception:
        return symbol, "No market data available."
    bars = series.bars
    price = bars[-1].close if bars else 0.0
    regime = classify_regime(series)
    vol = annualized_volatility(series)
    recent = (
        bars[-1].close / bars[-21].close - 1.0 if len(bars) > 21 and bars[-21].close else 0.0
    )
    return symbol, prompts.market_context(symbol, price, regime, vol, recent)


def parse_strategy_proposal(text: str) -> StrategyProposal | None:
    """Validate a model's strategy proposal. Returns None if unusable.

    The model's output is untrusted: only proposals that pass the pydantic
    schema (which bounds periods, indicators and allocation) are accepted.
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    try:
        return StrategyProposal.model_validate(data)
    except Exception:
        return None


async def run_research_cycle(
    session: AsyncSession,
    agent: Agent,
    *,
    question: ResearchQuestion,
    objective: str | None = None,
    symbol: str | None = None,
    interval: str = "1d",
    limit: int = 500,
) -> dict:
    """One full agent cycle: recall, propose, validate, learn."""
    objective = objective or agent.current_objective or question.question
    context_symbol, market_ctx = await _market_context(session, agent)
    target_symbol = symbol or context_symbol

    recalled = await memory.recall(
        session,
        agent_id=agent.id,
        query=f"{question.question} {objective} {target_symbol}",
        limit=8,
    )
    failures = [
        m["title"] for m in recalled if m["kind"] == MemoryKind.FAILURE.value
    ][:4]

    prompt = prompts.strategy_prompt(
        agent=agent,
        objective=objective,
        memories=recalled,
        market_context=market_ctx,
        prior_failures=failures,
    )
    scheduler = get_scheduler()
    response = await scheduler.submit(
        LLMRequest(
            system=prompts.SYSTEM_AGENT,
            prompt=scheduler.bound_context(prompt),
            temperature=0.7,
            json_mode=True,
        ),
        priority=4,
        agent_id=str(agent.id),
    )
    proposal = parse_strategy_proposal(response.text)

    agent.current_objective = objective
    await session.flush()

    if proposal is None:
        await memory.remember(
            session,
            agent_id=agent.id,
            kind=MemoryKind.FAILURE,
            title="Unparseable strategy proposal",
            content="The model returned a proposal that failed schema validation; "
            "it was discarded rather than guessed at.",
            importance=0.5,
            tags=["failure", "validation"],
            generation_number=agent.generation_number,
        )
        await events.emit(
            session,
            EventCategory.AGENT,
            "strategy_proposal_rejected",
            f"{agent.codename} produced an invalid strategy proposal; discarded.",
            source="agents",
            severity="WARNING",
            agent_id=agent.id,
        )
        return {"agent": agent.codename, "verdict": "INVALID_PROPOSAL"}

    hypothesis = Hypothesis(
        question_id=question.id,
        agent_id=agent.id,
        statement=proposal.thesis or f"{agent.specialization} edge on {proposal.symbol}",
        rationale=proposal.rationale,
        status="TESTING",
        prior_confidence=0.5,
        generation_number=agent.generation_number,
    )
    session.add(hypothesis)
    await session.flush()
    await memory.remember(
        session,
        agent_id=agent.id,
        kind=MemoryKind.HYPOTHESIS,
        title=f"Hypothesis on {proposal.symbol}",
        content=hypothesis.statement,
        importance=0.55,
        tags=["hypothesis", agent.specialization],
        source_type="hypothesis",
        source_id=hypothesis.id,
        generation_number=agent.generation_number,
    )

    strategy, version = await strategies.create_strategy(
        session,
        name=proposal.name,
        agent_id=agent.id,
        generation_number=agent.generation_number,
        thesis=proposal.thesis,
        creation_reason=f"Proposed by {agent.codename} for question '{question.question}'.",
        asset_universe=[proposal.symbol],
        time_horizon=proposal.time_horizon,
        parameters={"allocation_pct": proposal.allocation_pct},
        entry_rules=[r.model_dump() for r in proposal.entry_rules],
        exit_rules=[r.model_dump() for r in proposal.exit_rules],
        risk_rules={"max_allocation": proposal.allocation_pct},
    )

    # Validate the symbol the strategy actually trades, not the agent's default.
    summary = await validate_and_learn(
        session, agent, strategy, version, proposal.symbol, interval, limit
    )
    summary["agent"] = agent.codename
    summary["hypothesis_id"] = str(hypothesis.id)
    summary["strategy_name"] = strategy.name
    return summary


async def validate_and_learn(
    session: AsyncSession,
    agent: Agent,
    strategy,
    version,
    symbol: str,
    interval: str = "1d",
    limit: int = 500,
) -> dict:
    """Run the deterministic validation pipeline and record the lesson."""
    from ecosystem.services import experiments

    summary = await experiments.run_full_validation(
        session,
        strategy=strategy,
        version=version,
        agent_id=agent.id,
        generation_number=agent.generation_number,
        symbol=symbol,
        interval=interval,
        limit=limit,
    )
    verdict = summary.get("verdict")
    if verdict == "ELIGIBLE_FOR_PAPER_TRADING":
        await memory.remember(
            session,
            agent_id=agent.id,
            kind=MemoryKind.LESSON,
            title=f"Validated approach: {strategy.name}",
            content=(
                f"{strategy.thesis or ''} This survived backtest, out-of-sample and "
                f"robustness testing on {symbol}."
            ),
            importance=0.8,
            tags=["lesson", "success", symbol],
            source_type="strategy",
            source_id=strategy.id,
            generation_number=agent.generation_number,
        )
    else:
        await memory.remember(
            session,
            agent_id=agent.id,
            kind=MemoryKind.FAILURE,
            title=f"Rejected approach: {strategy.name}",
            content=(
                f"Rejected at {verdict}. In-sample score "
                f"{summary.get('in_sample_score', 0):.3f}. Do not repeat this rule set "
                f"unchanged."
            ),
            importance=0.7,
            tags=["failure", verdict or "unknown", symbol],
            source_type="strategy",
            source_id=strategy.id,
            generation_number=agent.generation_number,
        )
    return summary


async def propose_trade_from_strategy(
    session: AsyncSession,
    agent: Agent,
    strategy,
    *,
    interval: str = "1d",
    limit: int = 180,
) -> dict:
    """Turn a validated strategy's current signal into a trade proposal.

    The agent may only propose. Risk, accounting and a human still have to
    agree before anything executes.
    """
    from ecosystem.db.models.enums import ProposalAction
    from ecosystem.services import approval, risk

    if risk.emergency_stop_active():
        return {"agent": agent.codename, "verdict": "BLOCKED_EMERGENCY"}

    version = await strategies.active_version(session, strategy)
    if version is None:
        return {"agent": agent.codename, "verdict": "NO_ACTIVE_VERSION"}
    if strategy.stage != StrategyStage.PAPER_TRADING:
        return {"agent": agent.codename, "verdict": f"NOT_AUTHORIZED:{strategy.stage.value}"}

    symbol = strategy.asset_universe[0] if strategy.asset_universe else "BTCUSD"
    _, series = await market_data.get_or_load_series(session, symbol, interval, limit)
    closes = [b.close for b in series.bars]
    if not closes:
        return {"agent": agent.codename, "verdict": "NO_DATA"}

    should_hold = conditions_met(version.entry_rules, closes) and not conditions_met(
        version.exit_rules, closes
    )
    action = ProposalAction.BUY if should_hold else ProposalAction.SELL

    from ecosystem.db.models.portfolio import Portfolio

    result = await session.execute(
        select(Portfolio).where(Portfolio.agent_id == agent.id).limit(1)
    )
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        return {"agent": agent.codename, "verdict": "NO_PORTFOLIO"}

    price = closes[-1]
    if action == ProposalAction.BUY:
        allocation = float(version.parameters.get("allocation_pct", 0.1))
        cash = float(portfolio.cash)
        notional = min(cash * allocation, cash)
        quantity = notional / price if price > 0 else 0.0
    else:
        held = next((p for p in portfolio.positions if p.symbol == symbol), None)
        quantity = float(held.quantity) if held else 0.0

    if quantity <= 0:
        return {"agent": agent.codename, "verdict": "NO_ACTION"}

    proposal = await approval.create_trade_proposal(
        session,
        agent_id=agent.id,
        portfolio_id=portfolio.id,
        action=action,
        symbol=symbol,
        quantity=quantity,
        price=price,
        reason=f"{strategy.name} signal at {price:.4f} ({agent.specialization}).",
        strategy_id=strategy.id,
        strategy_stage=strategy.stage.value,
    )
    approval_request = await approval.submit_for_approval(session, proposal)
    return {
        "agent": agent.codename,
        "verdict": "PROPOSED" if approval_request else proposal.status.value,
        "proposal_id": str(proposal.id),
        "action": action.value,
        "symbol": symbol,
        "quantity": quantity,
    }


async def performance_snapshot(session: AsyncSession, agent: Agent) -> dict:
    """Summarise an agent's graded experiments for ranking and the UI."""
    from ecosystem.db.models.research import Experiment, ExperimentResult

    result = await session.execute(
        select(ExperimentResult)
        .join(Experiment, Experiment.id == ExperimentResult.experiment_id)
        .where(Experiment.agent_id == agent.id)
    )
    rows = result.scalars().all()
    metrics = [{"score": float(r.score), "passed": bool(r.passed)} for r in rows]
    return evaluate_agent(metrics, generation_median=0.5)


async def relate(
    session: AsyncSession,
    *,
    source_agent_id: uuid.UUID,
    target_agent_id: uuid.UUID,
    relation: str,
    weight: float = 0.5,
    note: str | None = None,
) -> AgentRelationship:
    """Record a relationship between agents for the Network graph."""
    result = await session.execute(
        select(AgentRelationship).where(
            AgentRelationship.source_agent_id == source_agent_id,
            AgentRelationship.target_agent_id == target_agent_id,
            AgentRelationship.relation == relation,
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        existing.interactions += 1
        existing.weight = max(0.0, min(1.0, weight))
        if note:
            existing.note = note
        return existing

    relationship = AgentRelationship(
        source_agent_id=source_agent_id,
        target_agent_id=target_agent_id,
        relation=relation,
        weight=max(0.0, min(1.0, weight)),
        interactions=1,
        note=note,
    )
    session.add(relationship)
    await session.flush()
    return relationship
