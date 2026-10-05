"""Risk limits, risk events, proposals, approvals and execution records."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ecosystem.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ecosystem.db.models.enums import (
    AdapterKind,
    ExecutionStatus,
    ProposalAction,
    ProposalKind,
    ProposalStatus,
    RiskDecision,
    RiskSeverity,
)
from ecosystem.db.models.types import JSON, NumericAsDecimal, Ratio


class RiskLimit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Deterministic, versioned risk limits. Agents have no write access."""

    __tablename__ = "risk_limits"
    __table_args__ = (
        UniqueConstraint("scope", "scope_id", "name", name="uq_risk_limits_scope_name"),
    )

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), default="SYSTEM", nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    limit_type: Mapped[str] = mapped_column(String(48), nullable=False)
    value: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), nullable=False)
    unit: Mapped[str] = mapped_column(String(16), default="ratio", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class RiskEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "risk_events"
    __table_args__ = (Index("ix_risk_events_decision_created", "decision", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision: Mapped[RiskDecision] = mapped_column(nullable=False)
    severity: Mapped[RiskSeverity] = mapped_column(
        default=RiskSeverity.INFO, nullable=False
    )
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trade_proposals.id", ondelete="SET NULL")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    observed_value: Mapped[float | None] = mapped_column(NumericAsDecimal(24, 8))
    limit_value: Mapped[float | None] = mapped_column(NumericAsDecimal(24, 8))
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class _ProposalBase(UUIDPrimaryKeyMixin, TimestampMixin):
    """Shared columns for trade and transfer proposals."""

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="SET NULL")
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    action: Mapped[ProposalAction] = mapped_column(nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[float] = mapped_column(NumericAsDecimal(28, 12), default=0, nullable=False)
    price: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    amount: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL")
    )
    status: Mapped[ProposalStatus] = mapped_column(
        default=ProposalStatus.DRAFT, nullable=False, index=True
    )
    # Annotated with the enum type explicitly: SQLAlchemy does not infer a
    # native enum for a nullable `Enum | None` column, and without it the value
    # would round-trip as a bare string.
    risk_decision: Mapped[RiskDecision | None] = mapped_column(
        Enum(RiskDecision, name="riskdecision", create_type=False)
    )
    risk_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    accounting_ok: Mapped[bool | None] = mapped_column(Boolean)
    accounting_summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TradeProposal(_ProposalBase, Base):
    __tablename__ = "trade_proposals"

    is_high_impact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    approvals: Mapped[list["ApprovalRequest"]] = relationship(
        back_populates="trade_proposal", foreign_keys="ApprovalRequest.trade_proposal_id"
    )


class TransferProposal(_ProposalBase, Base):
    __tablename__ = "transfer_proposals"

    source_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    destination_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="SET NULL")
    )
    is_high_impact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ApprovalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The single human-in-the-loop gate. AI cannot create an approved row."""

    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_requests_status_created", "status", "created_at"),
    )

    proposal_kind: Mapped[ProposalKind] = mapped_column(nullable=False)
    trade_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trade_proposals.id", ondelete="CASCADE")
    )
    transfer_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transfer_proposals.id", ondelete="CASCADE")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )

    status: Mapped[ProposalStatus] = mapped_column(
        default=ProposalStatus.PENDING_APPROVAL, nullable=False, index=True
    )
    is_high_impact: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    confirmation_phrase: Mapped[str | None] = mapped_column(String(64))

    decided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    # Snapshot of what the human saw, for audit reconstruction.
    request_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    trade_proposal: Mapped[TradeProposal | None] = relationship(
        back_populates="approvals", foreign_keys=[trade_proposal_id]
    )
    decided_by: Mapped["User | None"] = relationship(back_populates="approvals")


class ExecutionRecord(UUIDPrimaryKeyMixin, Base):
    """Result of handing an approved proposal to an execution adapter."""

    __tablename__ = "execution_records"
    __table_args__ = (Index("ix_execution_records_status_created", "status", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proposal_kind: Mapped[ProposalKind] = mapped_column(nullable=False)
    trade_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trade_proposals.id", ondelete="SET NULL")
    )
    transfer_proposal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transfer_proposals.id", ondelete="SET NULL")
    )
    approval_request_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="SET NULL")
    )
    adapter: Mapped[AdapterKind] = mapped_column(
        default=AdapterKind.PAPER_TRADING, nullable=False
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        default=ExecutionStatus.PENDING, nullable=False, index=True
    )
    symbol: Mapped[str | None] = mapped_column(String(32))
    requested_quantity: Mapped[float] = mapped_column(
        NumericAsDecimal(28, 12), default=0, nullable=False
    )
    filled_quantity: Mapped[float] = mapped_column(
        NumericAsDecimal(28, 12), default=0, nullable=False
    )
    fill_price: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    fee: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    slippage: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
