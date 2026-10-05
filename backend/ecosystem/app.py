"""FastAPI application entrypoint.

Run locally with:

    uvicorn ecosystem.app:app --reload --port 8000

The app is read-mostly by default: everything that changes state is guarded by
an authenticated human identity, and execution is deny-by-default.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ecosystem.api.routers import api_router
from ecosystem.config import get_settings
from ecosystem.db.session import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Importing the models here ensures metadata is populated for any tooling
    # that inspects the app, and surfaces import errors at startup.
    import ecosystem.db.models  # noqa: F401

    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ECOSYSTEM",
        description=(
            "Local-first AI research and portfolio-simulation ecosystem. "
            "Simulation only — no live capital is at risk."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # The frontend is served from a different port during development, and from
    # the Tauri custom protocol inside the desktop shell. Origins are restricted
    # to those local, non-public origins; nothing is exposed publicly.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
            # Tauri v2 webview origins (Linux/macOS use the custom scheme;
            # Windows uses the https variant).
            "tauri://localhost",
            "http://tauri.localhost",
            "https://tauri.localhost",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router)

    @app.get("/")
    async def root() -> dict:
        return {
            "name": "ECOSYSTEM",
            "version": "0.1.0",
            "environment": settings.environment,
            "docs": "/docs",
            "api": "/api",
            "simulation": True,
        }

    return app


app = create_app()
