"""Risk limits and the deterministic risk engine.

Limits are read-only through the API. The AI has no write path to them, and
changing them requires editing configuration and restarting, which is a
deliberate, auditable act.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session
from ecosystem.config import get_settings
from ecosystem.db.models.governance import RiskLimit
from ecosystem.db.models.identity import User
from ecosystem.services import risk

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/limits")
async def limits(
    agent_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    effective = await risk.effective_limits(session, agent_id)
    rows = (
        await session.execute(select(RiskLimit).order_by(RiskLimit.name))
    ).scalars().all()
    settings = get_settings()
    return {
        "effective": {
            "max_position_pct": str(effective.max_position_pct),
            "max_exposure_pct": str(effective.max_exposure_pct),
            "max_drawdown_pct": str(effective.max_drawdown_pct),
            "max_daily_loss_pct": str(effective.max_daily_loss_pct),
            "max_concentration_pct": str(effective.max_concentration_pct),
            "max_trades_per_day": effective.max_trades_per_day,
            "asset_allowlist": effective.asset_allowlist,
            "emergency_stop": effective.emergency_stop,
        },
        "overrides": [
            {
                "id": str(r.id),
                "name": r.name,
                "scope": r.scope,
                "scope_id": str(r.scope_id) if r.scope_id else None,
                "limit_type": r.limit_type,
                "value": str(r.value),
                "unit": r.unit,
                "enabled": r.enabled,
                "description": r.description,
            }
            for r in rows
        ],
        "authorized_symbols": settings.asset_allowlist if hasattr(settings, "asset_allowlist") else [],
        "emergency_stop": {
            "active": risk.emergency_stop_active(),
            "reason": risk.emergency_stop_reason(),
        },
    }
