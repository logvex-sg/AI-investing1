"""Accounts, portfolios, positions, balances and transactions.

Money is stored as NUMERIC and mutated only through the accounting service.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
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
from ecosystem.db.models.enums import AccountKind
from ecosystem.db.models.types import JSON, Money, NumericAsDecimal, Quantity, Ratio


class Account(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A cash account. Treasury accounts are system-owned; agent accounts are
    per-agent simulated investor accounts."""

    __tablename__ = "accounts"
    __table_args__ = (Index("ix_accounts_kind_agent", "kind", "agent_id"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[AccountKind] = mapped_column(nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(8), default="EUR", nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    starting_capital: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    cash: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    reserved_capital: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    realized_profit: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    unrealized_pnl: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    total_fees: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )

    balances: Mapped[list["Balance"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class Portfolio(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("agent_id", "name", name="uq_portfolios_agent_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    generation_number: Mapped[int | None] = mapped_column(Integer)
    base_currency: Mapped[str] = mapped_column(String(8), default="EUR", nullable=False)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Capital the portfolio began with. Never changes, so that "original
    # capital" can always be distinguished from profit.
    starting_capital: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )

    # Cached aggregates; recomputed by the portfolio service, never by an LLM.
    cash: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    reserved_capital: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    positions_value: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    total_value: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    peak_value: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    max_drawdown: Mapped[float] = mapped_column(Ratio, default=0, nullable=False)
    realized_profit: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    unrealized_pnl: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    total_fees: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    last_valued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    positions: Mapped[list["Position"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class Position(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "symbol", name="uq_positions_portfolio_symbol"),
    )

    portfolio_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(NumericAsDecimal(28, 12), default=0, nullable=False)
    average_cost: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    last_price: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    market_value: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    unrealized_pnl: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    realized_pnl: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")


class Balance(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-currency balance for an account (multi-currency support)."""

    __tablename__ = "balances"
    __table_args__ = (UniqueConstraint("account_id", "currency", name="uq_balances_account_currency"),)

    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(8), nullable=False)
    available: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    reserved: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)

    account: Mapped[Account] = relationship(back_populates="balances")


class Transaction(UUIDPrimaryKeyMixin, Base):
    """Double-entry style ledger line. Append-only."""

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_account_created", "account_id", "created_at"),
        Index("ix_transactions_portfolio_created", "portfolio_id", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("portfolios.id", ondelete="SET NULL")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(32))
    quantity: Mapped[float] = mapped_column(NumericAsDecimal(28, 12), default=0, nullable=False)
    price: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    gross_amount: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    fee: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), default=0, nullable=False)
    net_amount: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    balance_after: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    realized_pnl: Mapped[float] = mapped_column(
        NumericAsDecimal(24, 8), default=0, nullable=False
    )
    execution_record_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("execution_records.id", ondelete="SET NULL")
    )
    memo: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class ProfitRecord(UUIDPrimaryKeyMixin, Base):
    """Distinct ledger of realised profit, so original capital is never
    confused with earnings."""

    __tablename__ = "profit_records"

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    amount: Mapped[float] = mapped_column(NumericAsDecimal(24, 8), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), default="REALIZED", nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class PerformanceMetric(UUIDPrimaryKeyMixin, Base):
    """Point-in-time evaluation snapshot for an agent or the whole system."""

    __tablename__ = "performance_metrics"
    __table_args__ = (
        Index("ix_performance_metrics_scope_created", "scope", "scope_id", "created_at"),
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    generation_number: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    score: Mapped[float] = mapped_column(Ratio, default=0.0, nullable=False)
