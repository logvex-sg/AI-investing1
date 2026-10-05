"""Portfolio service: valuation and dashboard aggregation.

Every number here is derived from stored state and current marks. The LLM
never supplies a value.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecosystem.db.models.agents import Agent
from ecosystem.db.models.enums import AccountKind
from ecosystem.db.models.portfolio import (
    Account,
    Portfolio,
    Position,
    ProfitRecord,
    Transaction,
)
from ecosystem.domain.accounting_math import (
    allocation_fraction,
    concentration_ratio,
    exposure_fraction,
    total_return,
)
from ecosystem.domain.money import ZERO, money
from ecosystem.services import accounting, market_data

DEFAULT_UNIVERSE = ["EUR", "USD", "BTCUSD", "SPX"]


async def mark_portfolio(
    session: AsyncSession, portfolio: Portfolio, interval: str = "1d"
) -> Portfolio:
    """Revalue a portfolio using the latest marks for everything it holds."""
    symbols = [p.symbol for p in portfolio.positions if p.quantity > 0]
    prices = await market_data.latest_prices(session, symbols, interval)
    return await accounting.mark_to_market(
        session, portfolio, {k: money(v) for k, v in prices.items()}
    )


async def mark_all(session: AsyncSession, interval: str = "1d") -> int:
    """Revalue every portfolio. Used by the background valuation job."""
    result = await session.execute(select(Portfolio).options(selectinload(Portfolio.positions)))
    portfolios = list(result.scalars().all())
    for portfolio in portfolios:
        await mark_portfolio(session, portfolio, interval)
    return len(portfolios)


def _portfolio_summary(portfolio: Portfolio) -> dict:
    starting = money(portfolio.starting_capital)
    total = money(portfolio.total_value)
    return {
        "id": str(portfolio.id),
        "name": portfolio.name,
        "agent_id": str(portfolio.agent_id) if portfolio.agent_id else None,
        "generation_number": portfolio.generation_number,
        "base_currency": portfolio.base_currency,
        "is_simulated": portfolio.is_simulated,
        "starting_capital": str(starting),
        "cash": str(money(portfolio.cash)),
        "reserved_capital": str(money(portfolio.reserved_capital)),
        "positions_value": str(money(portfolio.positions_value)),
        "total_value": str(total),
        "realized_profit": str(money(portfolio.realized_profit)),
        "unrealized_pnl": str(money(portfolio.unrealized_pnl)),
        "total_fees": str(money(portfolio.total_fees)),
        "peak_value": str(money(portfolio.peak_value)),
        "max_drawdown": str(money(portfolio.max_drawdown)),
        "total_return": str(total_return(starting, total)),
        "last_valued_at": portfolio.last_valued_at.isoformat()
        if portfolio.last_valued_at
        else None,
    }


def _positions(portfolio: Portfolio, total_value: Decimal) -> list[dict]:
    rows = []
    for position in portfolio.positions:
        if position.quantity <= 0:
            continue
        value = money(position.market_value)
        rows.append(
            {
                "symbol": position.symbol,
                "quantity": str(position.quantity),
                "average_cost": str(money(position.average_cost)),
                "last_price": str(money(position.last_price)),
                "market_value": str(value),
                "unrealized_pnl": str(money(position.unrealized_pnl)),
                "realized_pnl": str(money(position.realized_pnl)),
                "allocation": str(allocation_fraction(value, total_value)),
            }
        )
    return sorted(rows, key=lambda r: Decimal(r["market_value"]), reverse=True)


async def system_overview(session: AsyncSession, interval: str = "1d") -> dict:
    """The Overview screen's data, assembled from the ledger."""
    await mark_all(session, interval)

    treasury_result = await session.execute(
        select(Account).where(Account.kind != AccountKind.AGENT)
    )
    treasury_accounts = list(treasury_result.scalars().all())

    portfolio_result = await session.execute(
        select(Portfolio)
        .options(selectinload(Portfolio.positions))
        .order_by(Portfolio.created_at)
    )
    portfolios = list(portfolio_result.scalars().all())

    starting = sum((money(p.starting_capital) for p in portfolios), ZERO)
    total = sum((money(p.total_value) for p in portfolios), ZERO)
    cash = sum((money(p.cash) for p in portfolios), ZERO)
    positions_value = sum((money(p.positions_value) for p in portfolios), ZERO)
    realized = sum((money(p.realized_profit) for p in portfolios), ZERO)
    unrealized = sum((money(p.unrealized_pnl) for p in portfolios), ZERO)
    fees = sum((money(p.total_fees) for p in portfolios), ZERO)
    worst_drawdown = max((money(p.max_drawdown) for p in portfolios), default=ZERO)

    treasury_value = sum((money(a.cash) for a in treasury_accounts), ZERO)
    system_total = money(total + treasury_value)

    return {
        "portfolio": {
            "starting_capital": str(starting),
            "total_value": str(total),
            "cash": str(cash),
            "positions_value": str(positions_value),
            "realized_profit": str(realized),
            "unrealized_pnl": str(unrealized),
            "total_fees": str(fees),
            "total_return": str(total_return(starting, total)),
            "max_drawdown": str(worst_drawdown),
            "exposure": str(exposure_fraction(positions_value, total)) if total else "0",
            "concentration": str(
                concentration_ratio([money(p.market_value) for p in _all_positions(portfolios)])
            ),
            "is_simulation": True,
        },
        "treasury": {
            "accounts": [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "kind": a.kind.value,
                    "cash": str(money(a.cash)),
                }
                for a in treasury_accounts
            ],
            "total": str(treasury_value),
        },
        "system_total": str(system_total),
        "portfolios": [_portfolio_summary(p) for p in portfolios],
    }


def _all_positions(portfolios: list[Portfolio]) -> list[Position]:
    out: list[Position] = []
    for portfolio in portfolios:
        out.extend([p for p in portfolio.positions if p.quantity > 0])
    return out


async def portfolio_detail(
    session: AsyncSession, portfolio_id: uuid.UUID, interval: str = "1d"
) -> dict:
    portfolio = await accounting.get_portfolio(session, portfolio_id)
    await mark_portfolio(session, portfolio, interval)
    await session.refresh(portfolio, ["positions"])

    total = money(portfolio.total_value)
    result = await session.execute(
        select(Transaction)
        .where(Transaction.portfolio_id == portfolio_id)
        .order_by(Transaction.created_at)
    )
    transactions = list(result.scalars().all())

    # Equity curve reconstructed from the immutable transaction log.
    equity_curve: list[dict] = []
    running = money(portfolio.starting_capital)
    equity_curve.append(
        {
            "timestamp": (portfolio.created_at.isoformat() if portfolio.created_at else ""),
            "value": str(running),
        }
    )
    for txn in transactions:
        running = money(running + money(txn.net_amount))
        equity_curve.append(
            {"timestamp": txn.created_at.isoformat(), "value": str(running)}
        )

    # Close the curve at the current mark rather than the last trade.
    equity_curve.append(
        {
            "timestamp": (
                portfolio.last_valued_at.isoformat()
                if portfolio.last_valued_at
                else datetime.now(timezone.utc).isoformat()
            ),
            "value": str(money(portfolio.total_value)),
        }
    )

    profits_result = await session.execute(
        select(ProfitRecord)
        .where(ProfitRecord.account_id == portfolio.account_id)
        .order_by(ProfitRecord.created_at.desc())
        .limit(50)
    )
    profits = list(profits_result.scalars().all())

    return {
        "portfolio": _portfolio_summary(portfolio),
        "positions": _positions(portfolio, total),
        "equity_curve": equity_curve,
        "transactions": [
            {
                "id": str(t.id),
                "created_at": t.created_at.isoformat(),
                "kind": t.kind,
                "symbol": t.symbol,
                "quantity": str(t.quantity),
                "price": str(t.price),
                "fee": str(t.fee),
                "net_amount": str(t.net_amount),
                "realized_pnl": str(t.realized_pnl),
                "balance_after": str(t.balance_after),
                "memo": t.memo,
            }
            for t in transactions[-200:]
        ],
        "profit_records": [
            {
                "id": str(p.id),
                "created_at": p.created_at.isoformat(),
                "amount": str(p.amount),
                "kind": p.kind,
                "symbol": p.symbol,
                "note": p.note,
            }
            for p in profits
        ],
    }


async def agent_contributions(session: AsyncSession) -> list[dict]:
    """Each agent's share of total portfolio value, for the Portfolio charts."""
    result = await session.execute(
        select(Portfolio, Agent)
        .join(Agent, Agent.id == Portfolio.agent_id, isouter=True)
        .order_by(Portfolio.total_value.desc())
    )
    rows = result.all()
    grand_total = sum((money(p.total_value) for p, _ in rows), ZERO)
    return [
        {
            "agent_id": str(agent.id) if agent else None,
            "codename": agent.codename if agent else "unassigned",
            "specialization": agent.specialization if agent else None,
            "total_value": str(money(portfolio.total_value)),
            "starting_capital": str(money(portfolio.starting_capital)),
            "realized_profit": str(money(portfolio.realized_profit)),
            "unrealized_pnl": str(money(portfolio.unrealized_pnl)),
            "contribution": str(allocation_fraction(money(portfolio.total_value), grand_total)),
        }
        for portfolio, agent in rows
    ]


async def accounting_reconciliation(session: AsyncSession) -> dict:
    """Verify every portfolio against its immutable transaction log."""
    result = await session.execute(select(Portfolio).options(selectinload(Portfolio.positions)))
    problems: list[dict] = []
    checked = 0
    for portfolio in result.scalars().all():
        checked += 1
        issues = await accounting.verify_invariants(session, portfolio)
        if issues:
            problems.append({"portfolio_id": str(portfolio.id), "issues": issues})
    return {"checked": checked, "problems": problems, "ok": not problems}
