"""Persistent agent memory with context-based retrieval.

Agents never receive their full history. `recall` embeds the current task and
pulls only the memories whose meaning is closest, blended with importance and
recency so that a crucial lesson is not buried by a recent trivial note.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.db.models.agents import AgentMemory
from ecosystem.db.models.enums import MemoryKind
from ecosystem.services.embeddings import embed

# Relative weights when ranking retrieved memories.
_W_SIMILARITY = 0.65
_W_IMPORTANCE = 0.25
_W_RECENCY = 0.10


async def remember(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    kind: MemoryKind,
    title: str,
    content: str,
    importance: float = 0.5,
    tags: list[str] | None = None,
    context: dict | None = None,
    source_type: str | None = None,
    source_id: uuid.UUID | None = None,
    generation_number: int | None = None,
) -> AgentMemory:
    """Persist a memory with its embedding so it can be retrieved later."""
    row = AgentMemory(
        agent_id=agent_id,
        kind=kind,
        title=title,
        content=content,
        importance=max(0.0, min(1.0, importance)),
        embedding=embed(f"{title}\n{content}"),
        tags=tags or [],
        context=context or {},
        source_type=source_type,
        source_id=source_id,
        generation_number=generation_number,
    )
    session.add(row)
    await session.flush()
    return row


async def recall(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID,
    query: str,
    limit: int = 8,
    kinds: list[MemoryKind] | None = None,
    min_importance: float = 0.0,
) -> list[dict]:
    """Return the memories most relevant to `query` for one agent."""
    query_vec = embed(query)
    stmt = select(AgentMemory).where(AgentMemory.agent_id == agent_id)
    if kinds:
        stmt = stmt.where(AgentMemory.kind.in_(kinds))
    if min_importance > 0:
        stmt = stmt.where(AgentMemory.importance >= min_importance)

    # pgvector's cosine distance gives us an indexed candidate set; we then
    # re-rank in Python with importance and recency folded in.
    stmt = stmt.order_by(AgentMemory.embedding.cosine_distance(query_vec)).limit(limit * 4)
    result = await session.execute(stmt)
    candidates = list(result.scalars().all())

    now = datetime.now(timezone.utc)
    scored: list[tuple[float, AgentMemory]] = []
    for memory in candidates:
        similarity = _cosine_similarity(memory.embedding, query_vec)
        age_days = max(0.0, (now - _aware(memory.created_at)).total_seconds() / 86400.0)
        recency = 1.0 / (1.0 + age_days / 30.0)
        score = (
            _W_SIMILARITY * similarity
            + _W_IMPORTANCE * float(memory.importance)
            + _W_RECENCY * recency
        )
        scored.append((score, memory))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "id": str(m.id),
            "kind": m.kind.value,
            "title": m.title,
            "content": m.content,
            "importance": float(m.importance),
            "tags": m.tags,
            "generation": m.generation_number,
            "relevance": round(score, 6),
            "created_at": _aware(m.created_at).isoformat(),
        }
        for score, m in scored[:limit]
    ]


def _cosine_similarity(a: list[float] | None, b: list[float]) -> float:
    if not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (na * nb)))


def _aware(dt: datetime) -> datetime:
    if dt is None:
        return datetime.now(timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def search(
    session: AsyncSession,
    *,
    agent_id: uuid.UUID | None = None,
    text: str | None = None,
    kinds: list[MemoryKind] | None = None,
    limit: int = 50,
) -> list[AgentMemory]:
    """Browse or text-search memories for the Memory tab."""
    stmt = select(AgentMemory)
    if agent_id is not None:
        stmt = stmt.where(AgentMemory.agent_id == agent_id)
    if kinds:
        stmt = stmt.where(AgentMemory.kind.in_(kinds))
    if text:
        pattern = f"%{text.lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(AgentMemory.title).like(pattern),
                func.lower(AgentMemory.content).like(pattern),
            )
        )
    stmt = stmt.order_by(AgentMemory.created_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def counts_by_kind(session: AsyncSession, agent_id: uuid.UUID) -> dict[str, int]:
    result = await session.execute(
        select(AgentMemory.kind, func.count())
        .where(AgentMemory.agent_id == agent_id)
        .group_by(AgentMemory.kind)
    )
    return {kind.value: count for kind, count in result.all()}


async def carry_forward(
    session: AsyncSession,
    *,
    from_agent_id: uuid.UUID,
    to_agent_id: uuid.UUID,
    kinds: list[MemoryKind] | None = None,
    limit: int = 12,
) -> int:
    """Copy the most important lessons from a parent to a child.

    Inheritance is deliberate and bounded: a child starts with distilled
    knowledge, not the parent's entire noisy history.
    """
    stmt = select(AgentMemory).where(AgentMemory.agent_id == from_agent_id)
    if kinds:
        stmt = stmt.where(AgentMemory.kind.in_(kinds))
    stmt = stmt.order_by(AgentMemory.importance.desc()).limit(limit)
    result = await session.execute(stmt)
    copied = 0
    for source in result.scalars().all():
        await remember(
            session,
            agent_id=to_agent_id,
            kind=source.kind,
            title=f"[inherited] {source.title}",
            content=source.content,
            importance=float(source.importance),
            tags=list(source.tags) + ["inherited"],
            context={**source.context, "inherited_from": str(from_agent_id)},
            source_type="agent_memory",
            source_id=source.id,
            generation_number=source.generation_number,
        )
        copied += 1
    return copied
