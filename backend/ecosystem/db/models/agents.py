"""Investor agents, generations, persistent memory and relationships."""

from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
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
    AgentStatus,
    GenerationStatus,
    MemoryKind,
)
from ecosystem.db.models.types import EMBEDDING_DIM, JSON, Ratio


class Generation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "generations"

    number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[GenerationStatus] = mapped_column(
        default=GenerationStatus.PLANNED, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_generation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("generations.id", ondelete="SET NULL")
    )
    # Aggregate stats computed deterministically from evaluated children.
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    creation_reason: Mapped[str | None] = mapped_column(Text)

    agents: Mapped[list["Agent"]] = relationship(
        back_populates="generation", foreign_keys="Agent.generation_id"
    )


class Agent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("codename", name="uq_agents_codename"),
        Index("ix_agents_generation_status", "generation_id", "status"),
    )

    codename: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    specialization: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[AgentStatus] = mapped_column(
        default=AgentStatus.ACTIVE, nullable=False, index=True
    )

    generation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("generations.id", ondelete="RESTRICT"), nullable=False
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)

    parent_a_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )
    parent_b_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="SET NULL")
    )

    # Inherited and mutated traits. Kept as structured JSON so the schema does
    # not need a migration every time a trait is added.
    personality: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    risk_profile: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    inherited_traits: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    mutations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    asset_preferences: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    time_horizon: Mapped[str] = mapped_column(String(32), default="medium", nullable=False)
    research_priorities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    creation_reason: Mapped[str | None] = mapped_column(Text)
    current_objective: Mapped[str | None] = mapped_column(Text)
    # use_alter breaks the agents <-> strategies reference cycle: the constraint
    # is emitted as a separate ALTER after both tables exist.
    current_strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "strategies.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_agents_current_strategy_id_strategies",
        ),
    )

    generation: Mapped[Generation] = relationship(
        back_populates="agents", foreign_keys=[generation_id]
    )
    parent_a: Mapped["Agent | None"] = relationship(
        remote_side="Agent.id", foreign_keys=[parent_a_id]
    )
    parent_b: Mapped["Agent | None"] = relationship(
        remote_side="Agent.id", foreign_keys=[parent_b_id]
    )
    memories: Mapped[list["AgentMemory"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    relationships_out: Mapped[list["AgentRelationship"]] = relationship(
        back_populates="source_agent",
        foreign_keys="AgentRelationship.source_agent_id",
        cascade="all, delete-orphan",
    )


class AgentMemory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Persistent, searchable agent memory.

    `embedding` backs semantic retrieval; `content` backs full-text search.
    Agents receive a small retrieved subset, never their whole history.
    """

    __tablename__ = "agent_memory"
    __table_args__ = (
        Index("ix_agent_memory_agent_kind", "agent_id", "kind"),
        Index(
            "ix_agent_memory_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[MemoryKind] = mapped_column(nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Ratio, default=0.5, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    context: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_type: Mapped[str | None] = mapped_column(String(48))
    source_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    generation_number: Mapped[int | None] = mapped_column(Integer)

    agent: Mapped[Agent] = relationship(back_populates="memories")


class AgentRelationship(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Directed, weighted relationship between two agents.

    Relationships are derived from real interactions (shared experiments,
    disagreement, inheritance) so the network graph reflects actual history.
    """

    __tablename__ = "agent_relationships"
    __table_args__ = (
        UniqueConstraint(
            "source_agent_id", "target_agent_id", "relation",
            name="uq_agent_relationships_edge",
        ),
    )

    source_agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    target_agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    weight: Mapped[float] = mapped_column(Ratio, default=0.5, nullable=False)
    interactions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    source_agent: Mapped[Agent] = relationship(
        back_populates="relationships_out", foreign_keys=[source_agent_id]
    )


class AgentEvent(UUIDPrimaryKeyMixin, Base):
    """Per-agent timeline of lifecycle and research events."""

    __tablename__ = "agent_events"
    __table_args__ = (Index("ix_agent_events_agent_created", "agent_id", "created_at"),)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
