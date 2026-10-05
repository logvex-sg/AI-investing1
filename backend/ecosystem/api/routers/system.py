"""System status, overview, market data and emergency control."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.api.deps import current_user, get_session, require_operator
from ecosystem.api.schemas import EmergencyStopRequest, MarkRequest
from ecosystem.config import get_settings
from ecosystem.db.models.agents import Agent, Generation
from ecosystem.db.models.enums import AgentStatus
from ecosystem.db.models.governance import ApprovalRequest, RiskEvent
from ecosystem.db.models.identity import SystemEvent, User
from ecosystem.db.models.market import MarketDataSeries
from ecosystem.db.models.portfolio import Portfolio
from ecosystem.db.models.research import Experiment
from ecosystem.services import market_data, portfolio, risk

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
async def health() -> dict:
    """Unauthenticated liveness probe. Deliberately reveals nothing sensitive."""
    settings = get_settings()
    return {
        "status": "ok",
        "environment": settings.environment,
        "llm_provider": settings.llm_provider,
        "execution_mode": "paper",
        "testnet_enabled": settings.enable_testnet_adapter,
        "production_enabled": settings.enable_production_adapter,
    }


@router.get("/status")
async def status(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    settings = get_settings()
    active_agents = (
        await session.execute(
            select(func.count())
            .select_from(Agent)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one()
    # The command center may be opened before the first generation is
    # bootstrapped; report that as setup-required rather than failing.
    generation_row = await _current_generation_or_none(session)
    pending = (
        await session.execute(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.status == "PENDING_APPROVAL")
        )
    ).scalar_one()
    experiments = (
        await session.execute(select(func.count()).select_from(Experiment))
    ).scalar_one()

    return {
        "environment": settings.environment,
        "llm_provider": settings.llm_provider,
        "generation": _generation_summary(generation_row),
        "active_agents": int(active_agents),
        "max_active_agents": settings.max_active_agents,
        "pending_approvals": int(pending),
        "experiments": int(experiments),
        "emergency_stop": {
            "active": risk.emergency_stop_active(),
            "reason": risk.emergency_stop_reason(),
        },
        "execution": {
            "default_adapter": "PAPER_TRADING",
            "testnet_enabled": settings.enable_testnet_adapter,
            "production_enabled": settings.enable_production_adapter,
        },
        "is_simulation": True,
        "setup_required": generation_row is None,
    }


async def _current_generation_or_none(session: AsyncSession) -> Generation | None:
    result = await session.execute(
        select(Generation).order_by(Generation.number.desc()).limit(1)
    )
    return result.scalar_one_or_none()


def _generation_summary(row: Generation | None) -> dict:
    if row is None:
        return {"number": None, "label": "not bootstrapped", "status": "SETUP_REQUIRED"}
    return {"number": row.number, "label": row.label, "status": row.status.value}


@router.get("/overview")
async def overview(
    interval: str = "1d",
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    data = await portfolio.system_overview(session, interval)
    generation_row = await _current_generation_or_none(session)
    observation = await _recent_counts(session)
    data["generation"] = _generation_summary(generation_row)
    data["setup_required"] = generation_row is None
    data["counts"] = observation
    data["emergency_stop"] = {
        "active": risk.emergency_stop_active(),
        "reason": risk.emergency_stop_reason(),
    }
    data["objective"] = await _current_objective(session)
    data["recent_events"] = await _recent_events(session)
    return data


async def _recent_events(session: AsyncSession, limit: int = 20) -> list[dict]:
    rows = (
        await session.execute(
            select(SystemEvent).order_by(SystemEvent.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "id": str(e.id),
            "created_at": e.created_at.isoformat() if e.created_at else None,
            "category": e.category.value,
            "event_type": e.event_type,
            "severity": e.severity,
            "message": e.message,
            "source": e.source,
            "agent_id": str(e.agent_id) if e.agent_id else None,
        }
        for e in rows
    ]


async def _recent_counts(session: AsyncSession) -> dict:
    agents = (
        await session.execute(
            select(func.count())
            .select_from(Agent)
            .where(Agent.status == AgentStatus.ACTIVE)
        )
    ).scalar_one()
    experiments = (
        await session.execute(select(func.count()).select_from(Experiment))
    ).scalar_one()
    pending = (
        await session.execute(
            select(func.count())
            .select_from(ApprovalRequest)
            .where(ApprovalRequest.status == "PENDING_APPROVAL")
        )
    ).scalar_one()
    risk_events = (
        await session.execute(select(func.count()).select_from(RiskEvent))
    ).scalar_one()
    return {
        "active_agents": int(agents),
        "experiments": int(experiments),
        "pending_approvals": int(pending),
        "risk_events": int(risk_events),
    }


async def _current_objective(session: AsyncSession) -> str | None:
    result = await session.execute(
        select(Agent.current_objective)
        .where(Agent.status == AgentStatus.ACTIVE, Agent.current_objective.is_not(None))
        .limit(1)
    )
    return result.scalar_one_or_none()


@router.get("/market/symbols")
async def market_symbols(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Supported symbols with whatever series have actually been ingested."""
    series_rows = (
        await session.execute(
            select(MarketDataSeries).order_by(MarketDataSeries.symbol, MarketDataSeries.interval)
        )
    ).scalars().all()
    loaded = {(s.symbol, s.interval): s for s in series_rows}

    symbols = []
    for symbol in market_data.supported_symbols():
        series = loaded.get((symbol, "1d"))
        symbols.append(
            {
                "symbol": symbol,
                "asset_class": series.asset_class if series else "unloaded",
                "interval": series.interval if series else "1d",
                "bars": series.bar_count if series else 0,
                "volatility": series.volatility_regime if series else None,
                "volatile": market_data.is_volatile(symbol),
                "source": series.source if series else None,
            }
        )
    return {"symbols": symbols}


@router.get("/market/{symbol}")
async def market_series(
    symbol: str,
    interval: str = "1d",
    limit: int = 500,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    series, bars = await market_data.get_or_load_series(session, symbol.upper(), interval, limit)
    return {
        "symbol": series.symbol,
        "interval": series.interval,
        "source": series.source,
        "bar_count": series.bar_count,
        "volatility_regime": series.volatility_regime,
        "volatile": market_data.is_volatile(series.symbol),
        "bars": [
            {
                "timestamp": bar.timestamp.isoformat(),
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
            }
            for bar in bars.bars
        ],
    }


@router.post("/emergency-stop")
async def emergency_stop(
    body: EmergencyStopRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Stop all new execution. Nothing is deleted and state is preserved."""
    await risk.engage_emergency_stop(session, reason=body.reason, actor=user.username)
    return {
        "active": True,
        "reason": body.reason,
        "message": "Emergency stop engaged. Execution is blocked; data is preserved.",
    }


@router.post("/emergency-stop/release")
async def release_emergency_stop(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    await risk.release_emergency_stop(
        session, reason=f"Released by {user.username}", actor=user.username
    )
    return {"active": False, "message": "Emergency stop released."}


@router.post("/mark")
async def mark(
    body: MarkRequest,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_operator),
) -> dict:
    """Revalue every portfolio from the latest marks."""
    count = await portfolio.mark_all(session, body.interval)
    return {"marked": count}


@router.get("/reconciliation")
async def reconciliation(
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Compare cached portfolio aggregates with the immutable ledger."""
    return await portfolio.accounting_reconciliation(session)
