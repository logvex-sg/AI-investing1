"""Proposal creation, accounting checks and human approval.

The pipeline is fixed and every stage records its outcome:

    AI PROPOSAL -> RISK -> ACCOUNTING CHECK -> HUMAN APPROVAL -> EXECUTION

Only a `User` row can move a proposal to APPROVED. There is no code path that
lets an agent set the approval status, and every decision is written to the
audit log with the deciding identity.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecosystem.db.models.agents import Agent
from ecosystem.db.models.enums import (
    AuditOutcome,
    EventCategory,
    ProposalAction,
    ProposalKind,
    ProposalStatus,
    RiskDecision,
)
from ecosystem.db.models.governance import (
    ApprovalRequest,
    TradeProposal,
    TransferProposal,
)
from ecosystem.db.models.identity import User
from ecosystem.db.models.portfolio import Account, Portfolio
from ecosystem.domain import risk_rules
from ecosystem.domain.accounting_math import available_capital
from ecosystem.domain.money import ZERO, money, to_decimal
from ecosystem.services import events, risk


class ApprovalError(Exception):
    """Raised when a proposal cannot advance through the pipeline."""


CONFIRMATION_PHRASE = "CONFIRM HIGH IMPACT"


def _snapshot_proposal(proposal: TradeProposal | TransferProposal) -> dict:
    """Everything the human needs to judge a proposal, captured at request
    time so the audit record cannot be rewritten later."""
    return {
        "id": str(proposal.id),
        "kind": "TRANSFER" if isinstance(proposal, TransferProposal) else "TRADE",
        "agent_id": str(proposal.agent_id) if proposal.agent_id else None,
        "action": proposal.action.value,
        "symbol": proposal.symbol,
        "quantity": str(proposal.quantity),
        "price": str(proposal.price),
        "amount": str(proposal.amount),
        "reason": proposal.reason,
        "risk_decision": proposal.risk_decision.value if proposal.risk_decision else None,
        "risk_summary": proposal.risk_summary,
        "accounting_ok": proposal.accounting_ok,
        "accounting_summary": proposal.accounting_summary,
        "is_high_impact": proposal.is_high_impact,
        "created_at": proposal.created_at.isoformat() if proposal.created_at else None,
    }


async def create_trade_proposal(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID | None,
    portfolio_id: uuid.UUID | None,
    action: ProposalAction,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    reason: str,
    strategy_id: uuid.UUID | None = None,
    strategy_stage: str | None = None,
) -> TradeProposal:
    """Register an AI proposal. It has no authority until risk, accounting and
    a human all agree."""
    quantity = to_decimal(quantity)
    price = to_decimal(price)
    proposal = TradeProposal(
        agent_id=agent_id,
        portfolio_id=portfolio_id,
        action=action,
        symbol=symbol.upper(),
        quantity=quantity,
        price=price,
        amount=money(quantity * price),
        reason=reason,
        strategy_id=strategy_id,
        status=ProposalStatus.PENDING_RISK,
        risk_summary={"strategy_stage": strategy_stage} if strategy_stage else {},
        accounting_summary={},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    session.add(proposal)
    await session.flush()
    await events.emit(
        session,
        EventCategory.AI,
        "proposal_created",
        f"{action.value} proposal for {quantity} {symbol.upper()}: {reason}",
        source="approval",
        agent_id=agent_id,
        payload={"proposal_id": str(proposal.id), "amount": str(proposal.amount)},
    )
    return proposal


async def create_transfer_proposal(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID | None,
    source_account_id: uuid.UUID,
    destination_account_id: uuid.UUID,
    amount: Decimal,
    reason: str,
) -> TransferProposal:
    """Register a transfer proposal between two accounts.

    Transfers move capital between treasury buckets or into an agent account;
    like trades they only become executable once risk, accounting and a human
    all agree.
    """
    amount = money(to_decimal(amount))
    if amount <= 0:
        raise ApprovalError("transfer amount must be positive")
    if source_account_id == destination_account_id:
        raise ApprovalError("source and destination accounts must differ")

    source = await session.get(Account, source_account_id)
    destination = await session.get(Account, destination_account_id)
    if source is None or destination is None:
        raise ApprovalError("source or destination account not found")

    portfolio = (
        await session.execute(
            select(Portfolio).where(Portfolio.account_id == source_account_id).limit(1)
        )
    ).scalar_one_or_none()

    # Transfers always require the confirmation phrase: they move capital
    # between buckets, which is exactly the kind of action a stray click must
    # not be able to perform.
    high_impact = risk_rules.is_high_impact(
        risk_rules.ProposalSnapshot(
            action=ProposalAction.TRANSFER.value,
            symbol=source.currency,
            quantity=amount,
            price=to_decimal(1),
            amount=amount,
        ),
        risk_rules.PortfolioSnapshot(
            cash=money(source.cash),
            reserved_capital=ZERO,
            positions_value=ZERO,
            peak_value=money(source.cash),
            starting_value=money(source.starting_capital),
            current_value=money(source.cash),
            day_start_value=money(source.cash),
        ),
    )

    proposal = TransferProposal(
        agent_id=agent_id,
        portfolio_id=portfolio.id if portfolio else None,
        account_id=source_account_id,
        action=ProposalAction.TRANSFER,
        symbol=source.currency,
        quantity=amount,
        price=to_decimal(1),
        amount=amount,
        reason=reason,
        source_account_id=source_account_id,
        destination_account_id=destination_account_id,
        is_high_impact=high_impact,
        status=ProposalStatus.PENDING_RISK,
        risk_summary={},
        accounting_summary={},
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    session.add(proposal)
    await session.flush()
    await events.emit(
        session,
        EventCategory.AI,
        "transfer_proposal_created",
        f"Transfer of {amount} {source.currency}: {reason}",
        source="approval",
        agent_id=agent_id,
        payload={"proposal_id": str(proposal.id), "amount": str(amount)},
    )
    return proposal


async def run_accounting_check(
    session: AsyncSession, proposal: TradeProposal | TransferProposal
) -> bool:
    """Verify the account can actually support the proposal.

    This is a solvency check, not a valuation: it confirms the cash and
    quantities exist. It never computes profit.
    """
    summary: dict = {}
    ok = True

    if proposal.action == ProposalAction.TRANSFER:
        if proposal.source_account_id and proposal.destination_account_id:
            source = await session.get(Account, proposal.source_account_id)
            destination = await session.get(Account, proposal.destination_account_id)
            if source is None or destination is None:
                ok = False
                summary["error"] = "source or destination account missing"
            elif source.cash < to_decimal(proposal.amount):
                ok = False
                summary["error"] = "insufficient funds"
                summary["available"] = str(source.cash)
        else:
            ok = False
            summary["error"] = "transfer requires source and destination"
    else:
        if proposal.portfolio_id is None:
            ok = False
            summary["error"] = "trade requires a portfolio"
        else:
            result = await session.execute(
                select(Portfolio)
                .options(selectinload(Portfolio.positions))
                .where(Portfolio.id == proposal.portfolio_id)
            )
            portfolio = result.scalar_one_or_none()
            if portfolio is None:
                ok = False
                summary["error"] = "portfolio not found"
            elif proposal.action == ProposalAction.BUY:
                spendable = available_capital(
                    money(portfolio.cash), money(portfolio.reserved_capital)
                )
                summary["available_capital"] = str(spendable)
                summary["required"] = str(money(proposal.amount))
                if to_decimal(proposal.amount) > spendable:
                    ok = False
                    summary["error"] = "insufficient available capital"
            elif proposal.action == ProposalAction.SELL:
                held = next(
                    (p for p in portfolio.positions if p.symbol == proposal.symbol), None
                )
                held_qty = to_decimal(held.quantity) if held else ZERO
                summary["held_quantity"] = str(held_qty)
                summary["sell_quantity"] = str(to_decimal(proposal.quantity))
                if held_qty < to_decimal(proposal.quantity):
                    ok = False
                    summary["error"] = "sell exceeds held quantity"
            summary["portfolio_value"] = str(money(portfolio.total_value))
            summary["cash"] = str(money(portfolio.cash))

    proposal.accounting_ok = ok
    proposal.accounting_summary = summary
    if not ok:
        proposal.status = ProposalStatus.ACCOUNTING_REJECTED
    elif proposal.status in (
        ProposalStatus.PENDING_ACCOUNTING,
        ProposalStatus.PENDING_RISK,
        ProposalStatus.DRAFT,
    ):
        proposal.status = ProposalStatus.PENDING_APPROVAL

    await events.emit(
        session,
        EventCategory.ACCOUNT,
        "accounting_check",
        f"Accounting check {'passed' if ok else 'failed'} for {proposal.symbol}.",
        source="approval",
        severity="INFO" if ok else "WARNING",
        agent_id=proposal.agent_id,
        payload={"proposal_id": str(proposal.id), "summary": summary},
    )
    return ok


async def submit_for_approval(
    session: AsyncSession, proposal: TradeProposal | TransferProposal
) -> ApprovalRequest | None:
    """Run risk and accounting, then create the human approval request.

    Returns None when an earlier stage rejected the proposal.
    """
    if risk.emergency_stop_active():
        proposal.status = ProposalStatus.BLOCKED_EMERGENCY
        await events.emit(
            session,
            EventCategory.RISK,
            "proposal_blocked_emergency",
            "Proposal blocked because the emergency stop is active.",
            source="approval",
            severity="CRITICAL",
            agent_id=proposal.agent_id,
            payload={"proposal_id": str(proposal.id)},
        )
        return None

    if isinstance(proposal, TradeProposal):
        verdict = await risk.evaluate_trade_proposal(session, proposal)
        if not verdict.passed:
            return None
    else:
        # Transfers have no market risk, but they still respect the latch and
        # require approval because they move capital between buckets.
        proposal.risk_decision = RiskDecision.PASS
        proposal.risk_summary = {"decision": "PASS", "violations": [], "warnings": []}

    if not await run_accounting_check(session, proposal):
        return None

    kind = (
        ProposalKind.TRANSFER
        if isinstance(proposal, TransferProposal)
        else ProposalKind.TRADE
    )
    approval = ApprovalRequest(
        proposal_kind=kind,
        trade_proposal_id=None if kind == ProposalKind.TRANSFER else proposal.id,
        transfer_proposal_id=proposal.id if kind == ProposalKind.TRANSFER else None,
        agent_id=proposal.agent_id,
        status=ProposalStatus.PENDING_APPROVAL,
        is_high_impact=proposal.is_high_impact,
        confirmation_phrase=CONFIRMATION_PHRASE if proposal.is_high_impact else None,
        request_snapshot=_snapshot_proposal(proposal),
    )
    session.add(approval)
    await session.flush()
    proposal.status = ProposalStatus.PENDING_APPROVAL

    await events.emit(
        session,
        EventCategory.AI,
        "approval_requested",
        f"Approval requested for {proposal.action.value} {proposal.symbol}"
        + (" (high impact)." if proposal.is_high_impact else "."),
        source="approval",
        severity="WARNING" if proposal.is_high_impact else "INFO",
        agent_id=proposal.agent_id,
        payload={"approval_id": str(approval.id), "proposal_id": str(proposal.id)},
    )
    return approval


async def decide(
    session: AsyncSession,
    *,
    approval_id: uuid.UUID,
    user_id: uuid.UUID,
    approve: bool,
    reason: str | None = None,
    confirmation: str | None = None,
) -> ApprovalRequest:
    """Record a human decision.

    The caller must be an authenticated `User`. High-impact proposals require
    the exact confirmation phrase, which is what makes an accidental click
    harmless.
    """
    result = await session.execute(
        select(ApprovalRequest).where(ApprovalRequest.id == approval_id)
    )
    approval = result.scalar_one_or_none()
    if approval is None:
        raise ApprovalError("approval request not found")

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise ApprovalError("deciding user not found or inactive")
    if approval.status != ProposalStatus.PENDING_APPROVAL:
        raise ApprovalError(f"approval is already {approval.status.value}")

    if approve and approval.is_high_impact:
        if (confirmation or "").strip() != CONFIRMATION_PHRASE:
            await events.audit(
                session,
                action="approval.confirm_phrase_missing",
                resource_type="approval_request",
                resource_id=str(approval.id),
                outcome=AuditOutcome.DENIED,
                actor_type="HUMAN",
                actor_id=str(user_id),
                reason="confirmation phrase missing or incorrect",
            )
            raise ApprovalError(
                f'High-impact approval requires the confirmation phrase: "{CONFIRMATION_PHRASE}"'
            )

    approval.decided_by_id = user_id
    approval.decided_at = datetime.now(timezone.utc)
    approval.decision_reason = reason
    approval.status = ProposalStatus.APPROVED if approve else ProposalStatus.REJECTED

    proposal = await _load_proposal(session, approval)
    if proposal is not None:
        proposal.status = approval.status

    await events.emit(
        session,
        EventCategory.AI,
        "approval_decided",
        f"{'Approved' if approve else 'Rejected'} by {user.username}"
        + (f": {reason}" if reason else "."),
        source="approval",
        severity="INFO" if approve else "WARNING",
        agent_id=approval.agent_id,
        payload={
            "approval_id": str(approval.id),
            "approved": approve,
            "user": user.username,
        },
    )
    await events.audit(
        session,
        action="approval.approve" if approve else "approval.reject",
        resource_type="approval_request",
        resource_id=str(approval.id),
        outcome=AuditOutcome.SUCCESS,
        actor_type="HUMAN",
        actor_id=str(user_id),
        reason=reason,
        detail={"snapshot": approval.request_snapshot},
    )
    return approval


async def _load_proposal(
    session: AsyncSession, approval: ApprovalRequest
) -> TradeProposal | TransferProposal | None:
    if approval.proposal_kind == ProposalKind.TRADE and approval.trade_proposal_id:
        return await session.get(TradeProposal, approval.trade_proposal_id)
    if approval.proposal_kind == ProposalKind.TRANSFER and approval.transfer_proposal_id:
        return await session.get(TransferProposal, approval.transfer_proposal_id)
    return None


async def list_pending(session: AsyncSession) -> list[ApprovalRequest]:
    result = await session.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.status == ProposalStatus.PENDING_APPROVAL)
        .order_by(ApprovalRequest.created_at.desc())
    )
    return list(result.scalars().all())


async def cancel_expired(session: AsyncSession) -> int:
    """Expire stale proposals so the queue does not fill with dead requests."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(TradeProposal).where(
            TradeProposal.status == ProposalStatus.PENDING_APPROVAL,
            TradeProposal.expires_at.is_not(None),
            TradeProposal.expires_at < now,
        )
    )
    count = 0
    for proposal in result.scalars().all():
        proposal.status = ProposalStatus.CANCELLED
        count += 1
    return count
