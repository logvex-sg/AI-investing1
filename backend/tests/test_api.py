"""HTTP API tests.

These exercise the real FastAPI application against a real PostgreSQL
database. The `get_session` dependency is overridden to the test transaction,
so requests see the same rolled-back world as the rest of the suite.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from ecosystem.api.deps import get_session
from ecosystem.app import app
from ecosystem.db.models.enums import UserRole
from ecosystem.services import auth, generation


@pytest_asyncio.fixture
async def client(session):
    async def _override():
        yield session

    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def operator(session):
    await generation.bootstrap(session)
    user = await auth.create_user(
        session,
        username=f"api-{uuid.uuid4().hex[:8]}",
        password="correct-horse-battery-staple",
        display_name="API Operator",
        role=UserRole.OPERATOR,
    )
    return user


@pytest_asyncio.fixture
async def token(client, operator) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": operator.username, "password": "correct-horse-battery-staple"},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_health_is_public(client):
    response = await client.get("/api/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["execution_mode"] == "paper"


async def test_protected_endpoint_requires_auth(client):
    response = await client.get("/api/system/status")
    assert response.status_code == 401


async def test_login_and_me(client, token, operator):
    response = await client.get("/api/auth/me", headers=_auth(token))
    assert response.status_code == 200
    assert response.json()["username"] == operator.username


async def test_status_before_bootstrap_is_setup_required(client, session):
    """The command center must load before the first generation exists."""
    user = await auth.create_user(
        session,
        username=f"pre-{uuid.uuid4().hex[:8]}",
        password="correct-horse-battery-staple",
        display_name="Pre-bootstrap Operator",
        role=UserRole.OPERATOR,
    )
    login = await client.post(
        "/api/auth/login",
        json={"username": user.username, "password": "correct-horse-battery-staple"},
    )
    assert login.status_code == 200, login.text
    headers = _auth(login.json()["token"])

    status = await client.get("/api/system/status", headers=headers)
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["setup_required"] is True
    assert body["generation"]["status"] == "SETUP_REQUIRED"
    assert body["generation"]["number"] is None

    overview = await client.get("/api/system/overview", headers=headers)
    assert overview.status_code == 200, overview.text
    assert overview.json()["setup_required"] is True

    current = await client.get("/api/generations/current", headers=headers)
    assert current.status_code == 404


async def test_overview_reports_simulation(client, token):
    response = await client.get("/api/system/overview", headers=_auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["portfolio"]["is_simulation"] is True
    assert "starting_capital" in body["portfolio"]
    assert body["counts"]["active_agents"] == 8


async def test_network_has_overseer_and_eight_agents(client, token):
    response = await client.get("/api/agents/network", headers=_auth(token))
    assert response.status_code == 200
    body = response.json()
    kinds = [n["kind"] for n in body["nodes"]]
    assert kinds.count("OVERSEER") == 1
    assert kinds.count("AGENT") == 8
    assert len(body["edges"]) >= 8


async def test_agent_detail_has_expected_tabs(client, token):
    listing = await client.get("/api/agents", headers=_auth(token))
    agent_id = listing.json()["agents"][0]["id"]
    response = await client.get(f"/api/agents/{agent_id}", headers=_auth(token))
    assert response.status_code == 200
    body = response.json()
    for key in ("performance", "memory_counts", "lineage", "portfolio"):
        assert key in body


async def test_proposal_pipeline_and_approval(client, token, session):
    listing = await client.get("/api/agents", headers=_auth(token))
    agent = listing.json()["agents"][0]

    proposal = await client.post(
        "/api/approvals/proposals/trade",
        headers=_auth(token),
        json={
            "agent_id": agent["id"],
            "portfolio_id": agent["portfolio"]["id"],
            "action": "BUY",
            "symbol": "BTCUSD",
            "quantity": "0.001",
            "price": "40000",
            "reason": "API pipeline test.",
        },
    )
    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    assert body["approval_request_id"] is not None
    assert body["status"] == "PENDING_APPROVAL"
    # Risk is evaluated against the authoritative market price, not the price
    # the requester asserted, so the stored amount reflects the real market.
    stored_price = await client.get(
        f"/api/approvals/{body['approval_request_id']}", headers=_auth(token)
    )
    assert stored_price.status_code == 200
    proposal_price = stored_price.json()["proposal"]["price"]
    assert proposal_price != "40000"

    pending = await client.get("/api/approvals/pending", headers=_auth(token))
    assert pending.json()["count"] == 1

    # A real market price on a small account makes this high impact, so the
    # human must supply the confirmation phrase.
    decision = await client.post(
        f"/api/approvals/{body['approval_request_id']}/decide",
        headers=_auth(token),
        json={
            "approve": True,
            "reason": "Looks reasonable.",
            "confirmation": "CONFIRM HIGH IMPACT",
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["status"] == "APPROVED"

    executed = await client.post(
        f"/api/approvals/proposals/{body['proposal_id']}/execute",
        headers=_auth(token),
        json={"adapter_kind": "PAPER_TRADING"},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["status"] == "FILLED"
    assert executed.json()["is_simulated"] is True


async def test_emergency_stop_blocks_new_execution(client, token):
    engage = await client.post(
        "/api/system/emergency-stop",
        headers=_auth(token),
        json={"reason": "Drill"},
    )
    assert engage.status_code == 200
    assert engage.json()["active"] is True

    status = await client.get("/api/system/status", headers=_auth(token))
    assert status.json()["emergency_stop"]["active"] is True

    release = await client.post(
        "/api/system/emergency-stop/release", headers=_auth(token)
    )
    assert release.status_code == 200
    assert release.json()["active"] is False


async def test_risk_limits_are_exposed_read_only(client, token):
    response = await client.get("/api/risk/limits", headers=_auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["effective"]["max_position_pct"] == "0.25"
    assert "max_trades_per_day" in body["effective"]


async def test_activity_stream_lists_events(client, token):
    response = await client.get("/api/activity", headers=_auth(token))
    assert response.status_code == 200
    assert "events" in response.json()
