"""Strategies and their immutable version history."""

from __future__ import annotations

import uuid

from sqlalchemy import (
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
from ecosystem.db.models.enums import StrategyStage
from ecosystem.db.models.types import JSON


class Strategy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A strategy lineage. The concrete definition lives in StrategyVersion."""

    __tablename__ = "strategies"
    __table_args__ = (Index("ix_strategies_agent_stage", "agent_id", "stage"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(140), unique=True, nullable=False)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    stage: Mapped[StrategyStage] = mapped_column(
        default=StrategyStage.IDEA, nullable=False, index=True
    )
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    parent_strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL")
    )
    asset_universe: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    time_horizon: Mapped[str] = mapped_column(String(32), default="medium", nullable=False)
    thesis: Mapped[str | None] = mapped_column(Text)
    creation_reason: Mapped[str | None] = mapped_column(Text)
    retired_reason: Mapped[str | None] = mapped_column(Text)

    versions: Mapped[list["StrategyVersion"]] = relationship(
        back_populates="strategy", cascade="all, delete-orphan", order_by="StrategyVersion.version"
    )


class StrategyVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An immutable snapshot of a strategy definition.

    Versions are never edited in place: a change produces a new row that
    records its parent and the reason for the mutation. This is what makes
    strategy evolution auditable.
    """

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
    )

    strategy_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)

    # Rule definitions. Deterministic interpreters read these; the LLM only
    # proposes them.
    parameters: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    entry_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    exit_rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    risk_rules: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    mutation_reason: Mapped[str | None] = mapped_column(Text)
    creation_reason: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)

    strategy: Mapped[Strategy] = relationship(back_populates="versions")
