"""Risk service.

Loads the state the pure rules need, runs them, and persists the verdict.
The decision itself is made by `ecosystem.domain.risk_rules`, never here, so
there is exactly one place where the rules live.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecosystem.config import get_settings
from ecosystem.db.models.agents import Agent
from ecosystem.db.models.enums import (
    EventCategory,
    ProposalStatus,
    RiskDecision,
    RiskSeverity,
)
from ecosystem.db.models.governance import RiskEvent, RiskLimit, TradeProposal
from ecosystem.db.models.portfolio import Portfolio, Transaction
from ecosystem.domain import risk_rules
from ecosystem.domain.money import money, to_decimal
from ecosystem.services import events

# Emergency stop is a process-wide latch. It is deliberately not stored in a
# table an agent could reach, and it is never cleared automatically.
_emergency_stop: bool = False
_emergency_reason: str | None = None


def emergency_stop_active() -> bool:
    return _emergency_stop


def emergency_stop_reason() -> str | None:
    return _emergency_reason


async def engage_emergency_stop(
    session: AsyncSession, reason: str, actor: str = "SYSTEM"
) -> None:
    """Halt all new execution. State is preserved; nothing is deleted."""
    global _emergency_stop, _emergency_reason
    _emergency_stop = True
    _emergency_reason = reason
    await events.emit(
        session,
        EventCategory.SECURITY,
        "emergency_stop_engaged",
        f"EMERGENCY STOP engaged by {actor}: {reason}",
        source="risk",
        severity="CRITICAL",
        payload={"reason": reason, "actor": actor},
    )
    await events.audit(
        session,
        action="emergency_stop.engage",
        resource_type="system",
        outcome="SUCCESS",
        actor_type=actor,
        reason=reason,
    )


async def release_emergency_stop(
    session: AsyncSession, reason: str, actor: str = "HUMAN"
) -> None:
    """Resume operation. Only a human-triggered action reaches this."""
    global _emergency_stop, _emergency_reason
    _emergency_stop = False
    _emergency_reason = None
    await events.emit(
        session,
        EventCategory.SECURITY,
        "emergency_stop_released",
        f"Emergency stop released by {actor}: {reason}",
        source="risk",
        severity="WARNING",
        payload={"reason": reason, "actor": actor},
    )
    await events.audit(
        session,
        action="emergency_stop.release",
        resource_type="system",
        outcome="SUCCESS",
        actor_type=actor,
        reason=reason,
    )


async def effective_limits(session: AsyncSession, agent_id: uuid.UUID | None = None) -> risk_rules.RiskLimits:
    """Merge configured defaults with any persisted per-scope overrides."""
    settings = get_settings()
    limits = risk_rules.RiskLimits(
        max_position_pct=to_decimal(settings.risk_max_position_pct),
        max_exposure_pct=to_decimal(settings.risk_max_exposure_pct),
        max_drawdown_pct=to_decimal(settings.risk_max_drawdown_pct),
        max_daily_loss_pct=to_decimal(settings.risk_max_daily_loss_pct),
        max_concentration_pct=to_decimal(settings.risk_max_concentration_pct),
        max_trades_per_day=settings.risk_max_trades_per_day,
        emergency_stop=_emergency_stop,
    )

    result = await session.execute(select(RiskLimit).where(RiskLimit.enabled.is_(True)))
    overrides: dict[str, Decimal] = {}
    for row in result.scalars().all():
        if row.scope == "AGENT" and row.scope_id not in (None, agent_id):
            continue
        overrides[row.name] = to_decimal(row.value)

    if not overrides:
        return limits

    return risk_rules.RiskLimits(
        max_position_pct=overrides.get("max_position_pct", limits.max_position_pct),
        max_exposure_pct=overrides.get("max_exposure_pct", limits.max_exposure_pct),
        max_drawdown_pct=overrides.get("max_drawdown_pct", limits.max_drawdown_pct),
        max_daily_loss_pct=overrides.get("max_daily_loss_pct", limits.max_daily_loss_pct),
        max_concentration_pct=overrides.get(
            "max_concentration_pct", limits.max_concentration_pct
        ),
        max_trades_per_day=int(
            overrides.get("max_trades_per_day", Decimal(limits.max_trades_per_day))
        ),
        asset_allowlist=limits.asset_allowlist,
        authorized_strategies=limits.authorized_strategies,
        emergency_stop=_emergency_stop,
    )


async def _trades_today(session: AsyncSession, portfolio_id: uuid.UUID) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=1)
    result = await session.execute(
        select(func.count())
        .select_from(Transaction)
        .where(
            Transaction.portfolio_id == portfolio_id,
            Transaction.kind.in_(("BUY", "SELL")),
            Transaction.created_at >= since,
        )
    )
    return int(result.scalar() or 0)


async def build_portfolio_snapshot(
    session: AsyncSession, portfolio: Portfolio
) -> risk_rules.PortfolioSnapshot:
    """Assemble the state the rules need from persisted data."""
    await session.refresh(portfolio, ["positions"])
    position_values = {
        p.symbol: money(p.market_value) for p in portfolio.positions if p.quantity > 0
    }
    positions_value = money(sum(position_values.values(), Decimal(0)))

    # Day-start value is reconstructed from the last transaction before today
    # rather than stored, so it cannot drift.
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    result = await session.execute(
        select(Transaction.balance_after)
        .where(Transaction.portfolio_id == portfolio.id, Transaction.created_at < midnight)
        .order_by(Transaction.created_at.desc())
        .limit(1)
    )
    day_start_cash = result.scalar_one_or_none()
    day_start_value = money(day_start_cash) if day_start_cash is not None else money(
        portfolio.starting_capital
    )
    if day_start_value <= 0:
        day_start_value = money(portfolio.starting_capital)

    return risk_rules.PortfolioSnapshot(
        cash=money(portfolio.cash),
        reserved_capital=money(portfolio.reserved_capital),
        positions_value=positions_value,
        peak_value=money(portfolio.peak_value),
        starting_value=money(portfolio.starting_capital),
        current_value=money(portfolio.total_value),
        day_start_value=day_start_value,
        position_values=position_values,
        trades_today=await _trades_today(session, portfolio.id),
    )


async def evaluate_trade_proposal(
    session: AsyncSession, proposal: TradeProposal
) -> risk_rules.RiskVerdict:
    """Run the rules for a proposal and persist the outcome."""
    if proposal.portfolio_id is None:
        verdict = risk_rules.RiskVerdict(
            "REJECT",
            [risk_rules.Violation("account_limits", "Proposal has no portfolio.")],
        )
        await _persist_verdict(session, proposal, verdict)
        return verdict

    result = await session.execute(
        select(Portfolio)
        .options(selectinload(Portfolio.positions))
        .where(Portfolio.id == proposal.portfolio_id)
    )
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        verdict = risk_rules.RiskVerdict(
            "REJECT",
            [risk_rules.Violation("account_limits", "Portfolio not found.")],
        )
        await _persist_verdict(session, proposal, verdict)
        return verdict

    limits = await effective_limits(session, proposal.agent_id)
    snapshot = await build_portfolio_snapshot(session, portfolio)
    proposal_snapshot = risk_rules.ProposalSnapshot(
        action=proposal.action.value if hasattr(proposal.action, "value") else str(proposal.action),
        symbol=proposal.symbol,
        quantity=to_decimal(proposal.quantity),
        price=to_decimal(proposal.price),
        amount=to_decimal(proposal.amount),
        strategy_stage=proposal.risk_summary.get("strategy_stage"),
    )
    verdict = risk_rules.evaluate(proposal_snapshot, snapshot, limits)
    proposal.is_high_impact = risk_rules.is_high_impact(proposal_snapshot, snapshot)
    await _persist_verdict(session, proposal, verdict)
    return verdict


async def _persist_verdict(
    session: AsyncSession,
    proposal: TradeProposal,
    verdict: risk_rules.RiskVerdict,
) -> None:
    # Coerce to the enum member rather than storing the bare string, so the
    # attribute is consistent before the next refresh.
    proposal.risk_decision = RiskDecision(verdict.decision)
    proposal.risk_summary = verdict.to_dict()
    if not verdict.passed:
        proposal.status = ProposalStatus.RISK_REJECTED
    elif proposal.status in (ProposalStatus.DRAFT, ProposalStatus.PENDING_RISK):
        proposal.status = ProposalStatus.PENDING_ACCOUNTING

    for violation in verdict.violations + verdict.warnings:
        is_violation = violation in verdict.violations
        session.add(
            RiskEvent(
                created_at=datetime.now(timezone.utc),
                decision=RiskDecision.REJECT if is_violation else RiskDecision.WARN,
                severity=RiskSeverity.CRITICAL if is_violation else RiskSeverity.WARNING,
                rule=violation.rule,
                message=violation.message,
                proposal_id=proposal.id,
                agent_id=proposal.agent_id,
                observed_value=violation.observed,
                limit_value=violation.limit,
                detail={},
            )
        )

    severity = "INFO" if verdict.decision == "PASS" else (
        "WARNING" if verdict.decision == "WARN" else "CRITICAL"
    )
    await events.emit(
        session,
        EventCategory.RISK,
        f"risk_{verdict.decision.lower()}",
        f"Risk engine {verdict.decision} for {proposal.symbol} {proposal.action.value}.",
        source="risk",
        severity=severity,
        agent_id=proposal.agent_id,
        payload={
            "proposal_id": str(proposal.id),
            "decision": verdict.decision,
            "violations": [v.rule for v in verdict.violations],
            "warnings": [v.rule for v in verdict.warnings],
        },
    )
