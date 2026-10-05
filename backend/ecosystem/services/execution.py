"""Execution adapters.

An adapter turns an *approved* proposal into a fill. Adapters are deny-by-
default: simulated and paper trading are always available, testnet requires an
explicit opt-in, and the production adapter refuses to run unless it has been
deliberately enabled and a separate guard is satisfied.

Nothing here trusts the caller: the service re-checks approval status and the
emergency latch before any adapter runs.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.config import get_settings
from ecosystem.db.models.enums import (
    AdapterKind,
    EventCategory,
    ExecutionStatus,
    ProposalAction,
    ProposalKind,
    ProposalStatus,
)
from ecosystem.db.models.governance import (
    ApprovalRequest,
    ExecutionRecord,
    TradeProposal,
    TransferProposal,
)
from ecosystem.db.models.portfolio import Portfolio
from ecosystem.domain.money import ZERO, money, to_decimal
from ecosystem.services import accounting, events, risk


class ExecutionError(Exception):
    """Raised when an execution is refused or fails."""


@dataclass(frozen=True)
class FillResult:
    status: ExecutionStatus
    filled_quantity: Decimal
    fill_price: Decimal
    fee: Decimal
    slippage: Decimal
    is_simulated: bool
    detail: dict


class ExecutionAdapter(Protocol):
    kind: AdapterKind

    async def execute(
        self, proposal: TradeProposal, *, market_price: Decimal | None = None
    ) -> FillResult: ...


class SimulatedExecutionAdapter:
    """Fills at the proposal price with a deterministic slippage haircut.

    Used by tests and by the backtester's accounting replay. It never touches
    a network and never reads live prices.
    """

    kind = AdapterKind.SIMULATED

    def __init__(self, slippage_bps: Decimal = Decimal("5"), fee_bps: Decimal = Decimal("10")):
        self.slippage_bps = to_decimal(slippage_bps)
        self.fee_bps = to_decimal(fee_bps)

    async def execute(
        self, proposal: TradeProposal, *, market_price: Decimal | None = None
    ) -> FillResult:
        price = money(market_price if market_price is not None else proposal.price)
        qty = to_decimal(proposal.quantity)
        is_buy = proposal.action.value == "BUY"
        adverse = price * self.slippage_bps / Decimal(10_000)
        fill_price = money(price + adverse if is_buy else price - adverse)
        notional = money(qty * fill_price)
        fee = money(notional * self.fee_bps / Decimal(10_000))
        return FillResult(
            status=ExecutionStatus.FILLED,
            filled_quantity=qty,
            fill_price=fill_price,
            fee=fee,
            slippage=money(qty * adverse),
            is_simulated=True,
            detail={"adapter": "simulated", "reference_price": str(price)},
        )


class PaperTradingExecutionAdapter(SimulatedExecutionAdapter):
    """Paper trading against a supplied live mark.

    Behaves like the simulated adapter but takes its reference price from the
    market data service, so paper results track the real market without any
    capital at risk.
    """

    kind = AdapterKind.PAPER_TRADING

    async def execute(
        self, proposal: TradeProposal, *, market_price: Decimal | None = None
    ) -> FillResult:
        if market_price is None:
            raise ExecutionError("paper trading requires a current market price")
        result = await super().execute(proposal, market_price=market_price)
        return FillResult(
            status=result.status,
            filled_quantity=result.filled_quantity,
            fill_price=result.fill_price,
            fee=result.fee,
            slippage=result.slippage,
            is_simulated=True,
            detail={**result.detail, "adapter": "paper"},
        )


class TestnetExecutionAdapter:
    """Placeholder for a testnet integration.

    Disabled unless `ECOSYSTEM_ENABLE_TESTNET_ADAPTER=true`. It is isolated
    from the production adapter and still only moves test funds.
    """

    kind = AdapterKind.TESTNET

    def __init__(self, endpoint: str | None = None):
        self.endpoint = endpoint

    async def execute(
        self, proposal: TradeProposal, *, market_price: Decimal | None = None
    ) -> FillResult:
        settings = get_settings()
        if not settings.enable_testnet_adapter:
            raise ExecutionError(
                "Testnet adapter is disabled. Set ECOSYSTEM_ENABLE_TESTNET_ADAPTER=true "
                "to opt in."
            )
        raise ExecutionError(
            "Testnet adapter is not wired to a provider in this build. "
            "Implement the provider client before enabling it."
        )


class ProductionExecutionAdapter:
    """Deliberately unimplemented.

    A production adapter must never be reachable by accident, so this raises
    unconditionally and is additionally gated behind an explicit setting.
    """

    kind = AdapterKind.PRODUCTION

    async def execute(
        self, proposal: TradeProposal, *, market_price: Decimal | None = None
    ) -> FillResult:
        settings = get_settings()
        if not settings.enable_production_adapter:
            raise ExecutionError(
                "Production execution is disabled. This build cannot move real funds."
            )
        raise ExecutionError(
            "Production execution is intentionally not implemented. "
            "Any real adapter must be built and reviewed separately."
        )


async def _settle_fill(
    session: AsyncSession,
    proposal: TradeProposal | TransferProposal,
    fill: Fill,
    record: ExecutionRecord,
) -> None:
    """Post an approved fill to the ledger.

    Buys and sells go through the accounting service so cash, positions and
    realised P&L stay consistent. Transfers move capital between accounts.
    """
    if isinstance(proposal, TransferProposal):
        if proposal.source_account_id is None or proposal.destination_account_id is None:
            raise ExecutionError("transfer proposal is missing an account")
        await accounting.transfer(
            session,
            source_account_id=proposal.source_account_id,
            destination_account_id=proposal.destination_account_id,
            amount=to_decimal(proposal.amount),
            memo=proposal.reason,
        )
        return

    if proposal.portfolio_id is None:
        raise ExecutionError("trade proposal is missing a portfolio")
    portfolio = await session.get(Portfolio, proposal.portfolio_id)
    if portfolio is None:
        raise ExecutionError("proposal portfolio not found")

    if proposal.action == ProposalAction.BUY:
        await accounting.apply_buy(
            session,
            portfolio,
            symbol=proposal.symbol,
            qty=fill.filled_quantity,
            price=fill.fill_price,
            fee=fill.fee,
            execution_record_id=record.id,
            memo=proposal.reason,
        )
    elif proposal.action == ProposalAction.SELL:
        await accounting.apply_sell(
            session,
            portfolio,
            symbol=proposal.symbol,
            qty=fill.filled_quantity,
            price=fill.fill_price,
            fee=fill.fee,
            execution_record_id=record.id,
            memo=proposal.reason,
        )
    # HOLD requires no ledger movement.


def adapter_for(kind: AdapterKind) -> ExecutionAdapter:
    if kind == AdapterKind.SIMULATED:
        return SimulatedExecutionAdapter()
    if kind == AdapterKind.PAPER_TRADING:
        return PaperTradingExecutionAdapter()
    if kind == AdapterKind.TESTNET:
        return TestnetExecutionAdapter()
    if kind == AdapterKind.PRODUCTION:
        return ProductionExecutionAdapter()
    raise ExecutionError(f"unknown adapter: {kind}")


async def execute_approved_trade(
    session: AsyncSession,
    proposal: TradeProposal,
    *,
    adapter_kind: AdapterKind = AdapterKind.PAPER_TRADING,
    market_price: Decimal | None = None,
) -> ExecutionRecord:
    """Execute a trade proposal. Refuses unless every gate is satisfied."""
    if risk.emergency_stop_active():
        raise ExecutionError("Emergency stop is active; execution is blocked.")

    is_transfer = isinstance(proposal, TransferProposal)
    approval_filter = (
        ApprovalRequest.transfer_proposal_id == proposal.id
        if is_transfer
        else ApprovalRequest.trade_proposal_id == proposal.id
    )
    result = await session.execute(
        select(ApprovalRequest).where(
            approval_filter, ApprovalRequest.status == ProposalStatus.APPROVED
        )
    )
    approval = result.scalars().first()
    if approval is None:
        raise ExecutionError("Proposal has no human approval; refusing to execute.")

    if proposal.status not in (ProposalStatus.APPROVED, ProposalStatus.EXECUTING):
        raise ExecutionError(
            f"Proposal status {proposal.status.value} is not executable."
        )

    adapter = adapter_for(adapter_kind)
    record = ExecutionRecord(
        created_at=datetime.now(timezone.utc),
        proposal_kind=ProposalKind.TRANSFER if is_transfer else ProposalKind.TRADE,
        trade_proposal_id=None if is_transfer else proposal.id,
        transfer_proposal_id=proposal.id if is_transfer else None,
        approval_request_id=approval.id,
        adapter=adapter_kind,
        status=ExecutionStatus.PENDING,
        symbol=proposal.symbol,
        requested_quantity=to_decimal(proposal.quantity),
        is_simulated=adapter_kind in (AdapterKind.SIMULATED, AdapterKind.PAPER_TRADING),
    )
    session.add(record)
    await session.flush()

    try:
        fill = await adapter.execute(proposal, market_price=market_price)
    except ExecutionError as exc:
        record.status = ExecutionStatus.FAILED
        record.error = str(exc)
        proposal.status = ProposalStatus.EXECUTION_FAILED
        await events.emit(
            session,
            EventCategory.TRADE,
            "execution_failed",
            f"Execution failed for {proposal.symbol}: {exc}",
            source="execution",
            severity="ERROR",
            agent_id=proposal.agent_id,
            payload={"proposal_id": str(proposal.id)},
        )
        await events.audit(
            session,
            action="execution.failed",
            resource_type="trade_proposal",
            resource_id=str(proposal.id),
            outcome="ERROR",
            actor_type="SYSTEM",
            reason=str(exc),
        )
        return record

    record.status = fill.status
    record.filled_quantity = fill.filled_quantity
    record.fill_price = fill.fill_price
    record.fee = fill.fee
    record.slippage = fill.slippage
    record.is_simulated = fill.is_simulated
    record.detail = fill.detail

    # Settle the fill in the ledger. A fill that is not posted to accounting is
    # not a trade, so this runs before the proposal is marked executed.
    try:
        await _settle_fill(session, proposal, fill, record)
    except Exception as exc:  # noqa: BLE001 - any posting failure is a failed execution
        record.status = ExecutionStatus.FAILED
        record.error = f"accounting settlement failed: {exc}"
        proposal.status = ProposalStatus.EXECUTION_FAILED
        await events.emit(
            session,
            EventCategory.ACCOUNT,
            "execution_settlement_failed",
            f"Settlement failed for {proposal.symbol}: {exc}",
            source="execution",
            severity="ERROR",
            agent_id=proposal.agent_id,
            payload={"proposal_id": str(proposal.id), "execution_id": str(record.id)},
        )
        await events.audit(
            session,
            action="execution.settlement_failed",
            resource_type="trade_proposal",
            resource_id=str(proposal.id),
            outcome="ERROR",
            actor_type="SYSTEM",
            reason=str(exc),
        )
        return record

    proposal.status = ProposalStatus.EXECUTED

    await events.emit(
        session,
        EventCategory.TRADE,
        "execution_filled",
        f"Filled {fill.filled_quantity} {proposal.symbol} at {fill.fill_price} "
        f"via {adapter_kind.value}.",
        source="execution",
        agent_id=proposal.agent_id,
        payload={
            "proposal_id": str(proposal.id),
            "execution_id": str(record.id),
            "fill_price": str(fill.fill_price),
            "simulated": fill.is_simulated,
        },
    )
    await events.audit(
        session,
        action="execution.filled",
        resource_type="trade_proposal",
        resource_id=str(proposal.id),
        outcome="SUCCESS",
        actor_type="SYSTEM",
        detail={"adapter": adapter_kind.value, "execution_id": str(record.id)},
    )
    return record
