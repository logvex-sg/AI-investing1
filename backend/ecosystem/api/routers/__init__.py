"""API routers."""

from __future__ import annotations

from fastapi import APIRouter

from ecosystem.api.routers import (
    activity,
    agents,
    approvals,
    auth_router,
    generations,
    memory_router,
    portfolio,
    research,
    risk_router,
    strategies,
    system,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(system.router)
api_router.include_router(auth_router.router)
api_router.include_router(agents.router)
api_router.include_router(generations.router)
api_router.include_router(strategies.router)
api_router.include_router(research.router)
api_router.include_router(portfolio.router)
api_router.include_router(approvals.router)
api_router.include_router(risk_router.router)
api_router.include_router(activity.router)
api_router.include_router(memory_router.router)

__all__ = ["api_router"]
