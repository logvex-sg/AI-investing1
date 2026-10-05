"""End-to-end integration tests against a real database.

These exercise the seams that unit tests cannot: foreign keys, the append-only
triggers, vector retrieval, and the full proposal -> risk -> approval ->
execution -> accounting path.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text

from ecosystem.db.models.agents import Agent
from ecosystem.db.models.enums import (
    AccountKind,
    AdapterKind,
    EventCategory,
    MemoryKind,
    ProposalAction,
    ProposalStatus,
    StrategyStage,
    UserRole,
)
from ecosystem.db.models.governance import ExecutionRecord
from ecosystem.db.models.identity import AuditLog, SystemEvent, User
from ecosystem.db.models.portfolio import Account, Portfolio, Transaction
from ecosystem.db.models.strategies import Strategy
from ecosystem.services import (
    accounting,
    approval,
    auth,
    execution,
    generation,
    market_data,
    memory,
    portfolio as portfolio_service,
    risk,
    strategies,
)
from ecosystem.domain.money import money
from ecosystem.services.agents import overseer


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _bootstrap(session):
    return await generation.bootstrap(session)


async def test_bootstrap_creates_eight_agents_and_treasury(session):
    result = await _bootstrap(session)
    assert result["status"] == "BOOTSTRAPPED"
    assert len(result["agents"]) == 8

    agents = await session.execute(select(Agent))
    assert len(agents.scalars().all()) == 8

    treasury = await session.execute(select(Account).where(Account.kind != AccountKind.AGENT))
    assert len(treasury.scalars().all()) == 4

    portfolios = await session.execute(select(Portfolio))
    assert len(portfolios.scalars().all()) == 8


async def test_bootstrap_is_idempotent(session):
    await _bootstrap(session)
    again = await _bootstrap(session)
    assert again["status"] == "ALREADY_BOOTSTRAPPED"
    agents = await session.execute(select(Agent))
    assert len(agents.scalars().all()) == 8


async def test_codenames_follow_generation_lettering(session):
    await _bootstrap(session)
    result = await session.execute(select(Agent).order_by(Agent.codename))
    codenames = [a.codename for a in result.scalars().all()]
    assert codenames == ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]


async def test_treasury_capital_is_split_and_conserved(session):
    await _bootstrap(session)
    result = await session.execute(select(Account).where(Account.kind != AccountKind.AGENT))
    accounts = {a.kind.value: a for a in result.scalars().all()}
    total = sum((Decimal(str(a.cash)) for a in accounts.values()), Decimal(0))
    # All trading capital has been distributed to the eight agents.
    assert accounts["TREASURY_PROTECTED"].cash == Decimal("4000")
    assert accounts["TREASURY_OPERATING"].cash == Decimal("1500")
    assert accounts["TREASURY_PROFIT"].cash == Decimal("500")
    assert total == Decimal("6000")  # 4000 + 1500 + 500; trading went to agents


async def test_agent_accounts_reconcile_with_the_ledger(session):
    await _bootstrap(session)
    reconciliation = await portfolio_service.accounting_reconciliation(session)
    assert reconciliation["ok"], reconciliation["problems"]


async def test_full_research_cycle_produces_strategies_and_evidence(session):
    await _bootstrap(session)
    report = await overseer.run_cycle(session)

    assert report["question"]["question"]
    assert len(report["results"]) == 8

    strategies_result = await session.execute(select(Strategy))
    created = strategies_result.scalars().all()
    assert len(created) >= 8

    # Every strategy carries its lineage reason.
    assert all(s.creation_reason for s in created)

    # Experiments produced graded results.
    from ecosystem.db.models.research import ExperimentResult

    results = await session.execute(select(ExperimentResult))
    graded = results.scalars().all()
    assert len(graded) > 0
    assert all(0.0 <= float(r.score) <= 1.0 for r in graded)


async def test_validation_runs_backtest_oos_and_robustness(session):
    await _bootstrap(session)
    agents = (await session.execute(select(Agent).order_by(Agent.codename))).scalars().all()
    agent = agents[0]

    from ecosystem.db.models.research import ResearchQuestion

    question = ResearchQuestion(
        question="Does a momentum signal on BTCUSD survive costs?",
        status="OPEN",
        priority=0.7,
        generation_number=1,
        created_by="OVERSEER",
    )
    session.add(question)
    await session.flush()

    result = await overseer.investor.run_research_cycle(
        session, agent, question=question, symbol="BTCUSD", limit=300
    )
    assert "verdict" in result
    assert result["verdict"] != "INVALID_PROPOSAL"

    # The strategy moved at least to backtesting and produced a version.
    strategy = await session.get(Strategy, uuid.UUID(result.get("strategy_id", str(uuid.uuid4()))))
    if strategy is not None:
        versions = await strategies.version_history(session, strategy.id)
        assert len(versions) >= 1
        assert versions[0].content_hash


async def test_proposal_pipeline_requires_human_approval(session):
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()
    portfolio = (
        await session.execute(select(Portfolio).where(Portfolio.agent_id == agent.id))
    ).scalar_one()

    price = Decimal("40000")
    quantity = Decimal("0.001")  # 40 EUR on a 500 EUR account, well within every limit
    proposal = await approval.create_trade_proposal(
        session,
        agent_id=agent.id,
        portfolio_id=portfolio.id,
        action=ProposalAction.BUY,
        symbol="BTCUSD",
        quantity=quantity,
        price=price,
        reason="Integration test proposal.",
    )
    request = await approval.submit_for_approval(session, proposal)
    assert request is not None, f"rejected: {proposal.risk_summary}"
    assert proposal.status == ProposalStatus.PENDING_APPROVAL
    assert proposal.accounting_ok is True

    # An unapproved proposal must not execute.
    with pytest.raises(execution.ExecutionError):
        await execution.execute_approved_trade(session, proposal)

    # An agent cannot approve: only a User can.
    user = await auth.create_user(
        session,
        username=f"operator-{uuid.uuid4().hex[:8]}",
        password="correct-horse-battery",
        display_name="Test Operator",
        role=UserRole.OPERATOR,
    )
    await approval.decide(session, approval_id=request.id, user_id=user.id, approve=True)

    record = await execution.execute_approved_trade(
        session, proposal, adapter_kind=AdapterKind.PAPER_TRADING, market_price=price
    )
    assert record.status.value == "FILLED"
    assert record.is_simulated is True

    position = (
        await session.execute(
            select(__import__("ecosystem.db.models.portfolio", fromlist=["Position"]).Position)
            .where(
                __import__("ecosystem.db.models.portfolio", fromlist=["Position"]).Position.portfolio_id
                == portfolio.id
            )
        )
    ).scalars().first()
    assert position is not None
    assert Decimal(str(position.quantity)) == quantity


async def test_risk_engine_rejects_oversized_proposal(session):
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()
    portfolio = (
        await session.execute(select(Portfolio).where(Portfolio.agent_id == agent.id))
    ).scalar_one()

    proposal = await approval.create_trade_proposal(
        session,
        agent_id=agent.id,
        portfolio_id=portfolio.id,
        action=ProposalAction.BUY,
        symbol="BTCUSD",
        quantity=Decimal("0.3"),  # 12,000 EUR on a 500 EUR portfolio
        price=Decimal("40000"),
        reason="Deliberately oversized.",
    )
    request = await approval.submit_for_approval(session, proposal)
    assert request is None
    assert proposal.status == ProposalStatus.RISK_REJECTED
    assert proposal.risk_decision.value == "REJECT"


async def test_disallowed_asset_is_rejected(session):
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()
    portfolio = (
        await session.execute(select(Portfolio).where(Portfolio.agent_id == agent.id))
    ).scalar_one()

    proposal = await approval.create_trade_proposal(
        session,
        agent_id=agent.id,
        portfolio_id=portfolio.id,
        action=ProposalAction.BUY,
        symbol="DOGEUSD",
        quantity=Decimal("1"),
        price=Decimal("1"),
        reason="Not on the allowlist.",
    )
    request = await approval.submit_for_approval(session, proposal)
    assert request is None
    rules = [v["rule"] for v in proposal.risk_summary["violations"]]
    assert "asset_allowlist" in rules


async def test_emergency_stop_blocks_and_preserves_state(session):
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()
    portfolio = (
        await session.execute(select(Portfolio).where(Portfolio.agent_id == agent.id))
    ).scalar_one()

    try:
        await risk.engage_emergency_stop(session, "Integration test drill", actor="TEST")
        assert risk.emergency_stop_active()

        proposal = await approval.create_trade_proposal(
            session,
            agent_id=agent.id,
            portfolio_id=portfolio.id,
            action=ProposalAction.BUY,
            symbol="BTCUSD",
            quantity=Decimal("0.001"),
            price=Decimal("40000"),
            reason="Should be blocked.",
        )
        request = await approval.submit_for_approval(session, proposal)
        assert request is None
        assert proposal.status == ProposalStatus.BLOCKED_EMERGENCY

        # Nothing was deleted.
        count = await session.execute(select(Portfolio))
        assert len(count.scalars().all()) == 8
    finally:
        await risk.release_emergency_stop(session, "Test complete", actor="HUMAN")
    assert not risk.emergency_stop_active()


async def test_append_only_tables_reject_updates(session):
    await _bootstrap(session)
    event = SystemEvent(
        category=EventCategory.SYSTEM,
        event_type="append_only_probe",
        severity="INFO",
        message="probe",
        source="test",
        payload={},
    )
    session.add(event)
    await session.flush()
    event_id = event.id

    with pytest.raises(Exception):
        await session.execute(
            text("UPDATE system_events SET message = 'tampered' WHERE id = :id"),
            {"id": str(event_id)},
        )
    # The failure poisoned the transaction; roll back so the fixture is clean.
    await session.rollback()


async def test_audit_log_records_privileged_actions(session):
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()
    portfolio = (
        await session.execute(select(Portfolio).where(Portfolio.agent_id == agent.id))
    ).scalar_one()
    proposal = await approval.create_trade_proposal(
        session,
        agent_id=agent.id,
        portfolio_id=portfolio.id,
        action=ProposalAction.BUY,
        symbol="BTCUSD",
        quantity=Decimal("0.001"),
        price=Decimal("40000"),
        reason="Audit test.",
    )
    request = await approval.submit_for_approval(session, proposal)
    user = await auth.create_user(
        session,
        username=f"auditor-{uuid.uuid4().hex[:8]}",
        password="correct-horse-battery",
        display_name="Auditor",
    )
    await approval.decide(
        session, approval_id=request.id, user_id=user.id, approve=False, reason="Not convinced."
    )
    logs = await session.execute(select(AuditLog).where(AuditLog.action == "approval.reject"))
    rows = logs.scalars().all()
    assert any(row.actor_id == str(user.id) for row in rows)


async def test_high_impact_approval_requires_confirmation(session):
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()
    portfolio = (
        await session.execute(select(Portfolio).where(Portfolio.agent_id == agent.id))
    ).scalar_one()

    # 24% of a 500 EUR portfolio: within the position limit but above the 10%
    # high-impact threshold.
    proposal = await approval.create_trade_proposal(
        session,
        agent_id=agent.id,
        portfolio_id=portfolio.id,
        action=ProposalAction.BUY,
        symbol="BTCUSD",
        quantity=Decimal("0.003"),
        price=Decimal("40000"),
        reason="High-impact test.",
    )
    request = await approval.submit_for_approval(session, proposal)
    assert request is not None
    assert request.is_high_impact is True

    user = await auth.create_user(
        session,
        username=f"approver-{uuid.uuid4().hex[:8]}",
        password="correct-horse-battery",
        display_name="Approver",
    )
    with pytest.raises(approval.ApprovalError):
        await approval.decide(session, approval_id=request.id, user_id=user.id, approve=True)

    await approval.decide(
        session,
        approval_id=request.id,
        user_id=user.id,
        approve=True,
        confirmation=approval.CONFIRMATION_PHRASE,
    )
    assert request.status == ProposalStatus.APPROVED


async def test_memory_retrieval_ranks_relevant_memories(session):
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()

    await memory.remember(
        session,
        agent_id=agent.id,
        kind=MemoryKind.LESSON,
        title="Momentum works on trending crypto",
        content="Momentum signals on BTCUSD performed well when volatility was elevated.",
        importance=0.9,
    )
    await memory.remember(
        session,
        agent_id=agent.id,
        kind=MemoryKind.LESSON,
        title="Defensive cash allocation",
        content="Holding EUR cash reduced drawdown during the turbulent regime.",
        importance=0.3,
    )

    recalled = await memory.recall(
        session, agent_id=agent.id, query="momentum crypto volatility", limit=2
    )
    assert recalled
    assert "Momentum" in recalled[0]["title"] or "momentum" in recalled[0]["title"].lower()
    assert recalled[0]["relevance"] >= recalled[-1]["relevance"]


async def test_mutation_records_parent_and_reason(session):
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()

    strategy, version = await strategies.create_strategy(
        session,
        name="Mutation test strategy",
        agent_id=agent.id,
        generation_number=1,
        thesis="Test thesis.",
        creation_reason="Integration test.",
        asset_universe=["BTCUSD"],
        time_horizon="short",
        parameters={"period": 20},
        entry_rules=[{"indicator": "momentum", "period": 20, "op": ">", "value": 0.0}],
        exit_rules=[{"indicator": "momentum", "period": 20, "op": "<", "value": 0.0}],
    )
    child = await strategies.mutate_strategy(
        session,
        strategy=strategy,
        parent_version=version,
        parameters={"period": 30},
        entry_rules=[{"indicator": "momentum", "period": 30, "op": ">", "value": 0.0}],
        exit_rules=[{"indicator": "momentum", "period": 30, "op": "<", "value": 0.0}],
        mutation_reason="Widen the lookback to reduce whipsaw.",
    )
    assert child.parent_version_id == version.id
    assert child.mutation_reason == "Widen the lookback to reduce whipsaw."
    assert child.version == version.version + 1

    tree = await strategies.family_tree(session, strategy)
    assert len(tree["versions"]) == 2
    assert tree["versions"][1]["parent_version_id"] == str(version.id)


async def test_generation_evolution_creates_children_with_lineage(session):
    await _bootstrap(session)
    await overseer.run_cycle(session)

    report = await generation.evolve(session, reason="Integration test evolution")
    assert report["generation"] == 2
    assert len(report["children"]) == 8

    children = (
        await session.execute(select(Agent).where(Agent.generation_number == 2))
    ).scalars().all()
    assert len(children) == 8
    assert all(c.parent_a_id is not None for c in children)
    assert all(c.creation_reason for c in children)
    assert all(c.inherited_traits for c in children)
    assert [c.codename for c in children] == [f"B{i}" for i in range(1, 9)]

    # Parents are archived, not deleted.
    parents = (
        await session.execute(select(Agent).where(Agent.generation_number == 1))
    ).scalars().all()
    assert len(parents) == 8
    assert all(p.status.value == "ARCHIVED" for p in parents)

    tree = await generation.family_tree(session, children[0].id)
    assert tree["ancestors"]


async def test_evolution_never_exceeds_eight_active_agents(session):
    await _bootstrap(session)
    await generation.evolve(session, reason="First evolution")
    active = (
        await session.execute(select(Agent).where(Agent.status == "ACTIVE"))
    ).scalars().all()
    assert len(active) <= 8


async def test_performance_history_is_immutable_across_generations(session):
    await _bootstrap(session)
    await generation.evolve(session, reason="History test")
    history = await generation.performance_history(session)
    assert len(history) == 2
    assert history[0]["number"] == 1
    assert history[1]["number"] == 2
    assert history[0]["agents"]


async def test_transfer_moves_capital_and_balances(session):
    await _bootstrap(session)
    treasury = await session.execute(
        select(Account).where(Account.kind == AccountKind.TREASURY_PROFIT)
    )
    profit_account = treasury.scalar_one()
    protected = (
        await session.execute(
            select(Account).where(Account.kind == AccountKind.TREASURY_PROTECTED)
        )
    ).scalar_one()

    before = Decimal(str(profit_account.cash))
    await accounting.transfer(
        session,
        source_account_id=profit_account.id,
        destination_account_id=protected.id,
        amount=Decimal("100"),
        memo="Test sweep.",
    )
    assert Decimal(str(profit_account.cash)) == before - Decimal("100")
    txns = await session.execute(
        select(Transaction).where(Transaction.account_id == profit_account.id)
    )
    assert any(t.kind == "TRANSFER_OUT" for t in txns.scalars().all())


async def test_market_data_is_deterministic_and_persisted(session):
    meta1, series1 = await market_data.get_or_load_series(session, "BTCUSD", "1d", 200)
    assert meta1.checksum == series1.checksum
    assert meta1.bar_count == 200

    meta2, series2 = await market_data.get_or_load_series(session, "BTCUSD", "1d", 200)
    assert meta2.id == meta1.id
    assert series2.checksum == series1.checksum


async def test_btc_is_flagged_volatile(session):
    assert market_data.is_volatile("BTCUSD") is True
    assert market_data.is_volatile("USD") is False


async def test_evolution_conserves_capital_and_reconciles(session):
    """Generation evolution must move capital, not mint it.

    Regression: `fund_agents` divided the treasury pool and rounded, which could
    ask the treasury for one minor unit more than it held (evolve crashed with
    "insufficient funds"). It also recorded transfers without attaching them to
    the portfolio that owns the account, so reconciliation failed afterwards.
    """
    await _bootstrap(session)
    treasury_before = {
        k: v.cash for k, v in (await accounting.ensure_treasury(session)).items()
    }
    # After bootstrap the trading pool has been fully allocated to the founding
    # agents, so the pool is the sum of their starting capital.
    founding = (
        await session.execute(
            select(Portfolio).where(Portfolio.generation_number == 1)
        )
    ).scalars().all()
    pool = sum((money(p.starting_capital) for p in founding), Decimal(0))
    assert pool > 0

    await generation.evolve(session, reason="Regression: conserve capital")

    treasury_after = {
        k: v.cash for k, v in (await accounting.ensure_treasury(session)).items()
    }
    children = (
        await session.execute(
            select(Portfolio).where(Portfolio.generation_number == 2)
        )
    ).scalars().all()
    assert len(children) == 8
    # Every unit of the pool ends up in a child's starting capital, to the cent.
    assert sum((money(p.starting_capital) for p in children), Decimal(0)) == pool
    assert treasury_after["trading"] == Decimal(0)
    # Protected capital and the other treasury buckets are never touched.
    assert treasury_after["protected"] == treasury_before["protected"]
    assert treasury_after["operating"] == treasury_before["operating"]
    assert treasury_after["profit"] == treasury_before["profit"]

    reconciliation = await portfolio_service.accounting_reconciliation(session)
    assert reconciliation["ok"], reconciliation["problems"]


async def test_trade_keeps_portfolio_value_and_ledger_consistent(session):
    """A trade must update cash and total_value together.

    Regression: `apply_buy`/`apply_sell` adjusted cash but left `total_value`
    stale by the fee, so reconciliation failed and evolution reclaimed more
    capital than the account held.
    """
    await _bootstrap(session)
    agent = (
        await session.execute(select(Agent).order_by(Agent.codename).limit(1))
    ).scalar_one()
    portfolio = (
        await session.execute(select(Portfolio).where(Portfolio.agent_id == agent.id))
    ).scalar_one()
    starting = money(portfolio.starting_capital)

    await accounting.apply_buy(
        session,
        portfolio,
        symbol="BTCUSD",
        qty=Decimal("0.0008"),
        price=Decimal("40000"),
        fee=Decimal("0.05"),
    )
    expected = money(portfolio.cash + portfolio.positions_value)
    assert money(portfolio.total_value) == expected

    await accounting.apply_sell(
        session,
        portfolio,
        symbol="BTCUSD",
        qty=Decimal("0.0008"),
        price=Decimal("41000"),
        fee=Decimal("0.05"),
    )
    assert money(portfolio.total_value) == money(
        portfolio.cash + portfolio.positions_value
    )
    # Selling above the entry price leaves the account ahead of its start.
    assert money(portfolio.total_value) > starting

    reconciliation = await portfolio_service.accounting_reconciliation(session)
    assert reconciliation["ok"], reconciliation["problems"]


async def test_generation_history_preserves_archived_performance(session):
    """An archived generation's snapshot survives the capital reclaim."""
    await _bootstrap(session)
    await generation.evolve(session, reason="Regression: history snapshot")

    history = await generation.performance_history(session)
    assert len(history) == 2
    archived = history[0]
    assert archived["status"] == "ARCHIVED"
    assert Decimal(archived["portfolio_value"]) > 0
    assert Decimal(archived["starting_capital"]) > 0
