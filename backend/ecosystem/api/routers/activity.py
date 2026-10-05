"""The activity stream, audit log and risk events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session
from ecosystem.db.models.enums import EventCategory
from ecosystem.db.models.governance import RiskEvent
from ecosystem.db.models.identity import AuditLog, SystemEvent, User
from ecosystem.services import auth, events

router = APIRouter(prefix="/activity", tags=["activity"])


def _event_row(event: SystemEvent) -> dict:
    return {
        "id": str(event.id),
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "category": event.category.value,
        "event_type": event.event_type,
        "severity": event.severity,
        "message": event.message,
        "source": event.source,
        "agent_id": str(event.agent_id) if event.agent_id else None,
        "payload": event.payload,
    }


@router.get("")
async def list_events(
    category: EventCategory | None = None,
    severity: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(limit)
    if category is not None:
        stmt = stmt.where(SystemEvent.category == category)
    if severity:
        stmt = stmt.where(SystemEvent.severity == severity.upper())
    rows = list((await session.execute(stmt)).scalars().all())
    return {"events": [_event_row(e) for e in rows], "count": len(rows)}


@router.get("/audit")
async def audit_log(
    action: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    rows = list((await session.execute(stmt)).scalars().all())
    return {
        "audit": [
            {
                "id": str(a.id),
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "actor_type": a.actor_type,
                "actor_id": a.actor_id,
                "action": a.action,
                "resource_type": a.resource_type,
                "resource_id": a.resource_id,
                "outcome": a.outcome.value,
                "reason": a.reason,
                "detail": a.detail,
            }
            for a in rows
        ]
    }


@router.get("/risk-events")
async def risk_events(
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    rows = (
        await session.execute(
            select(RiskEvent).order_by(RiskEvent.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return {
        "risk_events": [
            {
                "id": str(r.id),
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "decision": r.decision.value,
                "severity": r.severity.value,
                "rule": r.rule,
                "message": r.message,
                "proposal_id": str(r.proposal_id) if r.proposal_id else None,
                "agent_id": str(r.agent_id) if r.agent_id else None,
                "observed_value": str(r.observed_value) if r.observed_value is not None else None,
                "limit_value": str(r.limit_value) if r.limit_value is not None else None,
            }
            for r in rows
        ]
    }


@router.get("/stream")
async def stream(
    request: Request,
    token: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Server-Sent Events feed of the live activity stream.

    EventSource cannot set an Authorization header, so the signed session token
    is accepted as a query parameter and verified here. Events are pushed from
    the in-process event bus; the persisted `/activity` endpoint remains the
    system of record.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        claims = auth.decode_session_token(token)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if await auth.get_user(session, _uuid.UUID(claims["sub"])) is None:
        raise HTTPException(status_code=401, detail="User is inactive.")

    queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=200)

    def handler(event: dict) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    events.subscribe(handler)

    async def generator() -> AsyncIterator[bytes]:
        try:
            yield b": connected\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    payload = json.dumps(event)
                    yield f"event: activity\ndata: {payload}\n\n".encode()
                except asyncio.TimeoutError:
                    yield b": keep-alive\n\n"
        finally:
            events.unsubscribe(handler)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
