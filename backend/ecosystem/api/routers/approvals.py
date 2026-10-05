"""Trade and transfer proposals, the approval queue and execution."""

from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session, require_operator
from ecosystem.api.schemas import (
    DecisionRequest,
    ExecuteRequest,
    TradeProposalRequest,
    TransferProposalRequest,
)
from ecosystem.db.models.enums import ProposalStatus
from ecosystem.db.models.governance import ApprovalRequest, ExecutionRecord, TradeProposal
from ecosystem.db.models.identity import User
from ecosystem.services import approval, execution, market_data

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _approval_row(
    request: ApprovalRequest, proposal: TradeProposal | None
) -> dict:
    snapshot = request.request_snapshot or {}
    return {
        "id": str(request.id),
        "proposal_kind": request.proposal_kind.value,
        "trade_proposal_id": str(request.trade_proposal_id)
        if request.trade_proposal_id
        else None,
        "transfer_proposal_id": str(request.transfer_proposal_id)
        if request.transfer_proposal_id
        else None,
        "agent_id": str(request.agent_id) if request.agent_id else None,
        "status": request.status.value,
        "is_high_impact": request.is_high_impact,
        "confirmation_phrase": request.confirmation_phrase,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
        "decision_reason": request.decision_reason,
        "created_at": request.created_at.isoformat() if request.created_at else None,
        "snapshot": snapshot,
        "proposal": (
            {
                "id": str(proposal.id),
                "action": proposal.action.value,
                "symbol": proposal.symbol,
                "quantity": str(proposal.quantity),
                "price": str(proposal.price),
                "amount": str(proposal.amount),
                "reason": proposal.reason,
                "status": proposal.status.value,
                "risk_decision": proposal.risk_decision.value
                if proposal.risk_decision
                else None,
                "risk_summary": proposal.risk_summary,
                "accounting_ok": proposal.accounting_ok,
                "accounting_summary": proposal.accounting_summary,
                "expires_at": proposal.expires_at.isoformat()
                if proposal.expires_at
                else None,
            }
            if proposal
            else None
        ),
    }


@router.get("/pending")
async def pending(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    await approval.cancel_expired(session)
    requests = await approval.list_pending(session)
    out = []
    for request in requests:
        proposal = (
            await session.get(TradeProposal, request.trade_proposal_id)
            if request.trade_proposal_id
            else None
        )
        out.append(_approval_row(request, proposal))
    return {"pending": out, "count": len(out)}


@router.get("/{approval_id}")
async def approval_detail(
    approval_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    request = await session.get(ApprovalRequest, approval_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    proposal = (
        await session.get(TradeProposal, request.trade_proposal_id)
        if request.trade_proposal_id
        else None
    )
    return _approval_row(request, proposal)


@router.post("/proposals/trade")
async def create_trade_proposal(
    body: TradeProposalRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Register a proposal and push it through risk and accounting.

    A human still has to approve; this endpoint never executes.
    """
    # Risk and accounting must see the price the trade will actually fill at,
    # not the requester's view of it. Otherwise a proposal could pass risk on a
    # low asserted price and then execute far above it.
    price = body.price
    try:
        market_price, _ = await market_data.latest_price(session, body.symbol)
        price = Decimal(str(market_price))
    except ValueError:
        pass

    try:
        proposal = await approval.create_trade_proposal(
            session,
            agent_id=body.agent_id,
            portfolio_id=body.portfolio_id,
            action=body.action,
            symbol=body.symbol,
            quantity=body.quantity,
            price=price,
            reason=body.reason,
            strategy_id=body.strategy_id,
            strategy_stage=body.strategy_stage,
        )
        request = await approval.submit_for_approval(session, proposal)
    except approval.ApprovalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "proposal_id": str(proposal.id),
        "status": proposal.status.value,
        "risk_decision": proposal.risk_decision.value if proposal.risk_decision else None,
        "risk_summary": proposal.risk_summary,
        "accounting_ok": proposal.accounting_ok,
        "accounting_summary": proposal.accounting_summary,
        "approval_request_id": str(request.id) if request else None,
        "is_high_impact": request.is_high_impact if request else proposal.is_high_impact,
        "message": (
            "Queued for human approval."
            if request
            else "Rejected before human approval; see risk/accounting summaries."
        ),
    }


@router.post("/proposals/transfer")
async def create_transfer_proposal(
    body: TransferProposalRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    try:
        proposal = await approval.create_transfer_proposal(
            session,
            agent_id=body.agent_id,
            source_account_id=body.source_account_id,
            destination_account_id=body.destination_account_id,
            amount=body.amount,
            reason=body.reason,
        )
        request = await approval.submit_for_approval(session, proposal)
    except approval.ApprovalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "proposal_id": str(proposal.id),
        "status": proposal.status.value,
        "accounting_ok": proposal.accounting_ok,
        "accounting_summary": proposal.accounting_summary,
        "approval_request_id": str(request.id) if request else None,
        "is_high_impact": proposal.is_high_impact,
        "message": "Queued for human approval." if request else "Rejected before approval.",
    }


@router.post("/{approval_id}/decide")
async def decide(
    approval_id: uuid.UUID,
    body: DecisionRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Record a human decision. Only an authenticated User can reach this."""
    try:
        request = await approval.decide(
            session,
            approval_id=approval_id,
            user_id=user.id,
            approve=body.approve,
            reason=body.reason,
            confirmation=body.confirmation,
        )
    except approval.ApprovalError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "approval_id": str(request.id),
        "status": request.status.value,
        "decided_at": request.decided_at.isoformat() if request.decided_at else None,
    }


@router.post("/proposals/{proposal_id}/execute")
async def execute(
    proposal_id: uuid.UUID,
    body: ExecuteRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Execute an approved proposal through the requested adapter."""
    proposal = await session.get(TradeProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="Trade proposal not found.")

    # Paper trading fills at the current market price. If the caller did not
    # supply one, use the deterministic market feed rather than the proposal's
    # own price, so execution reflects the market and not the requester's view.
    market_price = body.market_price
    if market_price is None and proposal.symbol:
        try:
            latest, _ = await market_data.latest_price(session, proposal.symbol)
            market_price = Decimal(str(latest))
        except ValueError:
            market_price = None
    try:
        record = await execution.execute_approved_trade(
            session,
            proposal,
            adapter_kind=body.adapter_kind,
            market_price=market_price,
        )
    except execution.ExecutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "execution_id": str(record.id),
        "status": record.status.value,
        "adapter": record.adapter.value,
        "is_simulated": record.is_simulated,
        "filled_quantity": str(record.filled_quantity or 0),
        "fill_price": str(record.fill_price or 0),
        "fee": str(record.fee or 0),
        "slippage": str(record.slippage or 0),
        "error": record.error,
        "proposal_status": proposal.status.value,
    }


@router.get("/executions/recent")
async def recent_executions(
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    rows = (
        await session.execute(
            select(ExecutionRecord)
            .order_by(ExecutionRecord.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return {
        "executions": [
            {
                "id": str(r.id),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "proposal_kind": r.proposal_kind.value,
                "symbol": r.symbol,
                "adapter": r.adapter.value,
                "status": r.status.value,
                "requested_quantity": str(r.requested_quantity or 0),
                "filled_quantity": str(r.filled_quantity or 0),
                "fill_price": str(r.fill_price or 0),
                "fee": str(r.fee or 0),
                "is_simulated": r.is_simulated,
                "error": r.error,
            }
            for r in rows
        ]
    }
