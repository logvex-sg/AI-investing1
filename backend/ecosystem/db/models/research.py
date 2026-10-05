"""Research questions, hypotheses, experiments and their measured results."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ecosystem.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ecosystem.db.models.enums import ExperimentStage, ExperimentStatus
from ecosystem.db.models.types import JSON, Ratio


class ResearchQuestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A question the Overseer wants answered, with a priority and owner."""

    __tablename__ = "research_questions"
    __table_args__ = (Index("ix_research_questions_status_priority", "status", "priority"),)

    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="OPEN", nullable=False)
    priority: Mapped[float] = mapped_column(Ratio, default=0.5, nullable=False)
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(32), default="OVERSEER", nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conclusion: Mapped[str | None] = mapped_column(Text)

    hypotheses: Mapped[list["Hypothesis"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class Hypothesis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "hypotheses"

    question_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("research_questions.id", ondelete="SET NULL")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="PROPOSED", nullable=False)
    prior_confidence: Mapped[float] = mapped_column(Ratio, default=0.5, nullable=False)
    posterior_confidence: Mapped[float | None] = mapped_column(Ratio)
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)

    question: Mapped[ResearchQuestion | None] = relationship(back_populates="hypotheses")


class Experiment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A concrete, reproducible test assigned by the Overseer."""

    __tablename__ = "experiments"
    __table_args__ = (
        Index("ix_experiments_status_stage", "status", "stage"),
        Index("ix_experiments_agent_created", "agent_id", "created_at"),
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("hypotheses.id", ondelete="SET NULL")
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL")
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("strategy_versions.id", ondelete="SET NULL")
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL"), index=True
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)

    stage: Mapped[ExperimentStage] = mapped_column(
        default=ExperimentStage.BACKTEST, nullable=False
    )
    status: Mapped[ExperimentStatus] = mapped_column(
        default=ExperimentStatus.PROPOSED, nullable=False, index=True
    )
    priority: Mapped[float] = mapped_column(Ratio, default=0.5, nullable=False)

    # Deterministic specification: symbols, window, capital, seed. Two runs of
    # the same spec must produce identical numbers.
    spec: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[str] = mapped_column(String(32), default="OVERSEER", nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    results: Mapped[list["ExperimentResult"]] = relationship(
        back_populates="experiment", cascade="all, delete-orphan"
    )


class ExperimentResult(UUIDPrimaryKeyMixin, Base):
    """Machine-computed output of an experiment. Never authored by an LLM."""

    __tablename__ = "experiment_results"
    __table_args__ = (Index("ix_experiment_results_experiment", "experiment_id", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("experiments.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[ExperimentStage] = mapped_column(nullable=False)

    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    equity_curve: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    trades_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed: Mapped[bool] = mapped_column(default=False, nullable=False)
    score: Mapped[float] = mapped_column(Ratio, default=0.0, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    engine_version: Mapped[str] = mapped_column(String(32), default="1.0.0", nullable=False)
    deterministic_seed: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    experiment: Mapped[Experiment] = relationship(back_populates="results")


class Benchmark(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Benchmark series used for relative performance evaluation."""

    __tablename__ = "benchmarks"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    series: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    annualized_return: Mapped[float | None] = mapped_column(Float)
