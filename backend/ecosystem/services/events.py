"""System events and the audit log.

Every privileged action funnels through here so that the Activity screen and
the audit trail are the same stream of truth. Both tables are append-only at
the database level.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.db.models.enums import AuditOutcome, EventCategory
from ecosystem.db.models.identity import AuditLog, SystemEvent

# In-process subscribers. The API uses these to push live updates over SSE;
# they are advisory only and never the system of record.
_subscribers: list[Any] = []


def subscribe(handler: Any) -> None:
    _subscribers.append(handler)


def unsubscribe(handler: Any) -> None:
    if handler in _subscribers:
        _subscribers.remove(handler)


def _notify(event: dict) -> None:
    for handler in list(_subscribers):
        try:
            handler(event)
        except Exception:
            # A failing listener must never break the write path.
            pass


async def emit(
    session: AsyncSession,
    category: EventCategory,
    event_type: str,
    message: str,
    *,
    source: str = "system",
    severity: str = "INFO",
    agent_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    payload: dict | None = None,
) -> SystemEvent:
    """Record an event and notify live listeners."""
    event = SystemEvent(
        created_at=datetime.now(timezone.utc),
        category=category,
        event_type=event_type,
        severity=severity,
        message=message,
        source=source,
        agent_id=agent_id,
        correlation_id=correlation_id,
        payload=payload or {},
    )
    session.add(event)
    await session.flush()
    _notify(
        {
            "id": str(event.id),
            "created_at": event.created_at.isoformat(),
            "category": category.value,
            "event_type": event_type,
            "severity": severity,
            "message": message,
            "source": source,
            "agent_id": str(agent_id) if agent_id else None,
            "payload": payload or {},
        }
    )
    return event


async def audit(
    session: AsyncSession,
    *,
    action: str,
    resource_type: str,
    outcome: AuditOutcome,
    actor_type: str = "SYSTEM",
    actor_id: str | None = None,
    resource_id: str | None = None,
    reason: str | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Record a privileged operation. Denied attempts are recorded too."""
    row = AuditLog(
        created_at=datetime.now(timezone.utc),
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        reason=reason,
        detail=detail or {},
        ip_address=ip_address,
    )
    session.add(row)
    await session.flush()
    return row


async def security_event(
    session: AsyncSession,
    event_type: str,
    message: str,
    *,
    severity: str = "WARNING",
    detail: dict | None = None,
) -> SystemEvent:
    return await emit(
        session,
        EventCategory.SECURITY,
        event_type,
        message,
        source="security",
        severity=severity,
        payload=detail or {},
    )
