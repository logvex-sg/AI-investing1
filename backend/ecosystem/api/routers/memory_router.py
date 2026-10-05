"""Cross-agent memory search and inspection."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session
from ecosystem.api.schemas import MemorySearchRequest
from ecosystem.db.models.enums import MemoryKind
from ecosystem.db.models.identity import User
from ecosystem.services import memory

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("")
async def browse(
    agent_id: uuid.UUID | None = None,
    text: str | None = None,
    kind: MemoryKind | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    rows = await memory.search(
        session,
        agent_id=agent_id,
        text=text,
        kinds=[kind] if kind else None,
        limit=limit,
    )
    return {
        "memories": [
            {
                "id": str(m.id),
                "agent_id": str(m.agent_id),
                "kind": m.kind.value,
                "title": m.title,
                "content": m.content,
                "importance": float(m.importance),
                "tags": m.tags,
                "generation": m.generation_number,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in rows
        ]
    }


@router.post("/search")
async def semantic_search(
    body: MemorySearchRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Semantic recall. Requires an agent when searching one agent's memory."""
    if body.agent_id is None:
        rows = await memory.search(session, text=body.query, limit=body.limit)
        return {
            "memories": [
                {
                    "id": str(m.id),
                    "agent_id": str(m.agent_id),
                    "kind": m.kind.value,
                    "title": m.title,
                    "content": m.content,
                    "importance": float(m.importance),
                    "tags": m.tags,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                }
                for m in rows
            ]
        }
    recalled = await memory.recall(
        session, agent_id=body.agent_id, query=body.query, limit=body.limit
    )
    return {"memories": recalled}
