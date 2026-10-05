"""Accounting service.

The only component allowed to change a balance. It applies the pure maths in
`ecosystem.domain.accounting_math` to persisted state and writes an
append-only transaction for every movement. No LLM ever calls into this module
to set a number: proposals describe intent, this service computes the result.

Invariant enforced on every write: cash + reserved + positions value is
conserved except for explicitly recorded fees and realised P&L.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ecosystem.config import get_settings
from ecosystem.db.models.agents import Agent
from ecosystem.db.models.enums import AccountKind, EventCategory
from ecosystem.db.models.portfolio import (
    Account,
    Balance,
    Portfolio,
    Position,
    ProfitRecord,
    Transaction,
)
from ecosystem.domain.accounting_math import (
    AccountState,
    buy_cost,
    new_average_cost,
    position_market_value,
    position_unrealized_pnl,
    realized_pnl_on_sell,
    sell_proceeds,
    split_capital,
)
from ecosystem.domain.money import ZERO, money, quantity as qty_dp, to_decimal
from ecosystem.services import events

TREASURY_NAMES = {
    "protected": "System Treasury — Protected Capital",
    "trading": "System Treasury — Trading Allocation",
    "operating": "System Treasury — Operating Reserve",
    "profit": "System Treasury — Profit Reserve",
}

TREASURY_KINDS = {
    "protected": AccountKind.TREASURY_PROTECTED,
    "trading": AccountKind.TREASURY_TRADING,
    "operating": AccountKind.TREASURY_OPERATING,
    "profit": AccountKind.TREASURY_PROFIT,
}


class AccountingError(Exception):
    """Raised when a movement would violate the ledger's invariants."""


async def _get_or_create_balance(
    session: AsyncSession, account_id: uuid.UUID, currency: str
) -> Balance:
    result = await session.execute(
        select(Balance).where(
            Balance.account_id == account_id, Balance.currency == currency
        )
    )
    balance = result.scalar_one_or_none()
    if balance is None:
        balance = Balance(account_id=account_id, currency=currency, available=ZERO, reserved=ZERO)
        session.add(balance)
        await session.flush()
    return balance


async def ensure_treasury(
    session: AsyncSession, total_capital: Decimal | None = None
) -> dict[str, Account]:
    """Create the four treasury accounts and split the starting capital."""
    settings = get_settings()
    total = money(total_capital if total_capital is not None else settings.treasury_total_capital)
    splits = split_capital(
        total, {k: to_decimal(v) for k, v in settings.treasury_splits.items()}
    )

    accounts: dict[str, Account] = {}
    for key, kind in TREASURY_KINDS.items():
        result = await session.execute(
            select(Account).where(Account.kind == kind, Account.is_system.is_(True))
        )
        account = result.scalar_one_or_none()
        if account is None:
            account = Account(
                name=TREASURY_NAMES[key],
                kind=kind,
                currency=settings.base_currency,
                is_system=True,
                starting_capital=splits[key],
                cash=splits[key],
                reserved_capital=ZERO,
                realized_profit=ZERO,
                unrealized_pnl=ZERO,
                total_fees=ZERO,
            )
            session.add(account)
            await session.flush()
            await _get_or_create_balance(session, account.id, settings.base_currency)
            await events.emit(
                session,
                EventCategory.ACCOUNT,
                "treasury_account_created",
                f"{TREASURY_NAMES[key]} funded with {splits[key]} {settings.base_currency}.",
                source="accounting",
                payload={"account_id": str(account.id), "amount": str(splits[key])},
            )
        accounts[key] = account
    return accounts


async def create_agent_account(
    session: AsyncSession,
    agent: Agent,
    capital: Decimal,
    *,
    name: str | None = None,
) -> tuple[Account, Portfolio]:
    """Open a simulated investor account and portfolio for an agent."""
    settings = get_settings()
    capital = money(capital)
    account = Account(
        name=name or f"{agent.codename} — Trading Account",
        kind=AccountKind.AGENT,
        currency=settings.base_currency,
        agent_id=agent.id,
        is_system=False,
        starting_capital=capital,
        cash=capital,
        reserved_capital=ZERO,
        realized_profit=ZERO,
        unrealized_pnl=ZERO,
        total_fees=ZERO,
    )
    session.add(account)
    await session.flush()
    await _get_or_create_balance(session, account.id, settings.base_currency)

    portfolio = Portfolio(
        name=f"{agent.codename} Portfolio",
        agent_id=agent.id,
        account_id=account.id,
        generation_number=agent.generation_number,
        base_currency=settings.base_currency,
        is_simulated=True,
        starting_capital=capital,
        cash=capital,
        reserved_capital=ZERO,
        positions_value=ZERO,
        total_value=capital,
        peak_value=capital,
        max_drawdown=ZERO,
        realized_profit=ZERO,
        unrealized_pnl=ZERO,
        total_fees=ZERO,
    )
    session.add(portfolio)
    await session.flush()

    await events.emit(
        session,
        EventCategory.ACCOUNT,
        "agent_account_opened",
        f"{agent.codename} opened a simulated account with {capital} {settings.base_currency}.",
        source="accounting",
        agent_id=agent.id,
        payload={"account_id": str(account.id), "portfolio_id": str(portfolio.id)},
    )
    return account, portfolio


async def get_portfolio(session: AsyncSession, portfolio_id: uuid.UUID) -> Portfolio:
    result = await session.execute(
        select(Portfolio)
        .options(selectinload(Portfolio.positions))
        .where(Portfolio.id == portfolio_id)
    )
    portfolio = result.scalar_one_or_none()
    if portfolio is None:
        raise AccountingError(f"portfolio {portfolio_id} not found")
    return portfolio


async def _record_transaction(
    session: AsyncSession,
    *,
    account: Account,
    portfolio: Portfolio | None,
    agent_id: uuid.UUID | None,
    kind: str,
    symbol: str | None,
    qty: Decimal,
    price: Decimal,
    gross: Decimal,
    fee: Decimal,
    net: Decimal,
    realized_pnl: Decimal,
    memo: str | None = None,
    execution_record_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> Transaction:
    txn = Transaction(
        created_at=datetime.now(timezone.utc),
        account_id=account.id,
        portfolio_id=portfolio.id if portfolio else None,
        agent_id=agent_id,
        kind=kind,
        symbol=symbol,
        quantity=qty_dp(qty),
        price=money(price),
        gross_amount=money(gross),
        fee=money(fee),
        net_amount=money(net),
        balance_after=money(account.cash),
        realized_pnl=money(realized_pnl),
        execution_record_id=execution_record_id,
        memo=memo,
        detail=detail or {},
    )
    session.add(txn)
    await session.flush()
    return txn


async def apply_buy(
    session: AsyncSession,
    portfolio: Portfolio,
    *,
    symbol: str,
    qty: Decimal,
    price: Decimal,
    fee: Decimal,
    execution_record_id: uuid.UUID | None = None,
    memo: str | None = None,
) -> Position:
    """Open or increase a position, deducting cash and capitalising the fee."""
    settings = get_settings()
    qty = qty_dp(qty)
    price = money(price)
    fee = money(fee)
    if qty <= 0:
        raise AccountingError("buy quantity must be positive")
    total_cost = buy_cost(qty, price, fee)
    if total_cost > portfolio.cash:
        raise AccountingError(
            f"insufficient cash: need {total_cost}, have {portfolio.cash}"
        )

    result = await session.execute(
        select(Position).where(
            Position.portfolio_id == portfolio.id, Position.symbol == symbol
        )
    )
    position = result.scalar_one_or_none()
    if position is None:
        position = Position(
            portfolio_id=portfolio.id,
            symbol=symbol,
            quantity=ZERO,
            average_cost=ZERO,
            last_price=price,
            market_value=ZERO,
            unrealized_pnl=ZERO,
            realized_pnl=ZERO,
            opened_at=datetime.now(timezone.utc),
        )
        session.add(position)
        await session.flush()

    position.average_cost = new_average_cost(
        position.quantity, position.average_cost, qty, price, fee
    )
    position.quantity = qty_dp(position.quantity + qty)
    position.last_price = price
    position.market_value = position_market_value(position.quantity, price)
    position.unrealized_pnl = position_unrealized_pnl(
        position.quantity, price, position.average_cost
    )

    account = await session.get(Account, portfolio.account_id)
    if account is None:
        raise AccountingError("portfolio has no account")
    account.cash = money(account.cash - total_cost)
    account.total_fees = money(account.total_fees + fee)
    balance = await _get_or_create_balance(session, account.id, portfolio.base_currency)
    balance.available = money(balance.available - total_cost)

    portfolio.cash = money(portfolio.cash - total_cost)
    portfolio.total_fees = money(portfolio.total_fees + fee)
    await session.flush()
    await recompute_aggregates(session, portfolio)

    await _record_transaction(
        session,
        account=account,
        portfolio=portfolio,
        agent_id=portfolio.agent_id,
        kind="BUY",
        symbol=symbol,
        qty=qty,
        price=price,
        gross=money(qty * price),
        fee=fee,
        net=money(-total_cost),
        realized_pnl=ZERO,
        memo=memo,
        execution_record_id=execution_record_id,
        detail={"average_cost_after": str(position.average_cost)},
    )
    await events.emit(
        session,
        EventCategory.ACCOUNT,
        "buy_settled",
        f"Bought {qty} {symbol} at {price} (fee {fee}).",
        source="accounting",
        agent_id=portfolio.agent_id,
        payload={"portfolio_id": str(portfolio.id), "symbol": symbol, "quantity": str(qty)},
    )
    return position


async def apply_sell(
    session: AsyncSession,
    portfolio: Portfolio,
    *,
    symbol: str,
    qty: Decimal,
    price: Decimal,
    fee: Decimal,
    execution_record_id: uuid.UUID | None = None,
    memo: str | None = None,
) -> Position | None:
    """Reduce or close a position, crediting proceeds and booking realised P&L."""
    qty = qty_dp(qty)
    price = money(price)
    fee = money(fee)
    if qty <= 0:
        raise AccountingError("sell quantity must be positive")

    result = await session.execute(
        select(Position).where(
            Position.portfolio_id == portfolio.id, Position.symbol == symbol
        )
    )
    position = result.scalar_one_or_none()
    if position is None or position.quantity <= 0:
        raise AccountingError(f"no open position in {symbol}")
    if qty > position.quantity:
        raise AccountingError(
            f"cannot sell {qty} {symbol}: only {position.quantity} held"
        )

    proceeds = sell_proceeds(qty, price, fee)
    realized = realized_pnl_on_sell(qty, price, position.average_cost, fee)

    position.quantity = qty_dp(position.quantity - qty)
    position.last_price = price
    position.realized_pnl = money(position.realized_pnl + realized)
    if position.quantity <= 0:
        position.quantity = ZERO
        position.average_cost = ZERO
        position.market_value = ZERO
        position.unrealized_pnl = ZERO
    else:
        position.market_value = position_market_value(position.quantity, price)
        position.unrealized_pnl = position_unrealized_pnl(
            position.quantity, price, position.average_cost
        )

    account = await session.get(Account, portfolio.account_id)
    if account is None:
        raise AccountingError("portfolio has no account")
    account.cash = money(account.cash + proceeds)
    account.realized_profit = money(account.realized_profit + realized)
    account.total_fees = money(account.total_fees + fee)
    balance = await _get_or_create_balance(session, account.id, portfolio.base_currency)
    balance.available = money(balance.available + proceeds)

    portfolio.cash = money(portfolio.cash + proceeds)
    portfolio.realized_profit = money(portfolio.realized_profit + realized)
    portfolio.total_fees = money(portfolio.total_fees + fee)
    await session.flush()
    await recompute_aggregates(session, portfolio)

    txn = await _record_transaction(
        session,
        account=account,
        portfolio=portfolio,
        agent_id=portfolio.agent_id,
        kind="SELL",
        symbol=symbol,
        qty=qty,
        price=price,
        gross=money(qty * price),
        fee=fee,
        net=money(proceeds),
        realized_pnl=realized,
        memo=memo,
        execution_record_id=execution_record_id,
        detail={"average_cost": str(position.average_cost)},
    )

    # Realised profit is tracked separately so original capital is never
    # mistaken for earnings.
    session.add(
        ProfitRecord(
            created_at=datetime.now(timezone.utc),
            account_id=account.id,
            agent_id=portfolio.agent_id,
            amount=realized,
            kind="REALIZED",
            symbol=symbol,
            note=memo,
            detail={"transaction_id": str(txn.id)},
        )
    )

    await events.emit(
        session,
        EventCategory.ACCOUNT,
        "sell_settled",
        f"Sold {qty} {symbol} at {price} for realised P/L {realized}.",
        source="accounting",
        agent_id=portfolio.agent_id,
        payload={
            "portfolio_id": str(portfolio.id),
            "symbol": symbol,
            "quantity": str(qty),
            "realized_pnl": str(realized),
        },
    )
    return position


async def transfer(
    session: AsyncSession,
    *,
    source_account_id: uuid.UUID,
    destination_account_id: uuid.UUID,
    amount: Decimal,
    memo: str | None = None,
    destination_is_opening: bool = False,
) -> tuple[Transaction, Transaction | None]:
    """Move capital between accounts. Used for treasury funding and profit
    sweeping; normally recorded on both sides.

    `destination_is_opening` funds a brand-new account: the destination's cash
    rises but no transaction is recorded on its side, because the amount is its
    opening balance and is carried by `starting_capital`. Recording both would
    double-count it and break ledger reconstruction.
    """
    amount = money(amount)
    if amount <= 0:
        raise AccountingError("transfer amount must be positive")

    source = await session.get(Account, source_account_id)
    destination = await session.get(Account, destination_account_id)
    if source is None or destination is None:
        raise AccountingError("transfer account not found")
    if source.is_locked:
        raise AccountingError("source account is locked")
    if amount > source.cash:
        raise AccountingError(f"insufficient funds: {source.cash} < {amount}")

    source.cash = money(source.cash - amount)
    destination.cash = money(destination.cash + amount)
    if destination_is_opening:
        # The amount becomes the destination's opening balance.
        destination.starting_capital = money(destination.starting_capital + amount)
    src_balance = await _get_or_create_balance(session, source.id, source.currency)
    dst_balance = await _get_or_create_balance(session, destination.id, destination.currency)
    src_balance.available = money(src_balance.available - amount)
    dst_balance.available = money(dst_balance.available + amount)

    # Keep any portfolios attached to these accounts in step with the cash
    # movement, so valuation never reads a stale balance. The portfolio that
    # owns an account is also the portfolio that transaction is recorded
    # against, so per-portfolio ledger reconstruction stays exact when capital
    # leaves a trading account for the treasury (or arrives from it).
    side_portfolios: dict[uuid.UUID, Portfolio | None] = {}
    for account, delta in ((source, -amount), (destination, amount)):
        portfolio = (
            await session.execute(
                select(Portfolio).where(Portfolio.account_id == account.id)
            )
        ).scalar_one_or_none()
        side_portfolios[account.id] = portfolio
        if portfolio is not None:
            portfolio.cash = money(portfolio.cash + delta)
            portfolio.total_value = money(portfolio.total_value + delta)

    out_txn = await _record_transaction(
        session,
        account=source,
        portfolio=side_portfolios[source.id],
        agent_id=source.agent_id,
        kind="TRANSFER_OUT",
        symbol=None,
        qty=ZERO,
        price=ZERO,
        gross=amount,
        fee=ZERO,
        net=money(-amount),
        realized_pnl=ZERO,
        memo=memo,
        detail={"counterparty": str(destination.id)},
    )
    in_txn = None
    if not destination_is_opening:
        in_txn = await _record_transaction(
            session,
            account=destination,
            portfolio=side_portfolios[destination.id],
            agent_id=destination.agent_id,
            kind="TRANSFER_IN",
            symbol=None,
            qty=ZERO,
            price=ZERO,
            gross=amount,
            fee=ZERO,
            net=amount,
            realized_pnl=ZERO,
            memo=memo,
            detail={"counterparty": str(source.id)},
        )
    await events.emit(
        session,
        EventCategory.ACCOUNT,
        "transfer_settled",
        f"Transferred {amount} {source.currency} from {source.name} to {destination.name}.",
        source="accounting",
        payload={"amount": str(amount), "memo": memo},
    )
    return out_txn, in_txn


async def recompute_aggregates(session: AsyncSession, portfolio: Portfolio) -> Portfolio:
    """Rebuild a portfolio's cached totals from its positions and cash.

    Called after every cash or position mutation so `total_value` can never
    drift from its components (a trade that only adjusted `cash` would leave
    `total_value` stale by the fee, and anything summing portfolio value —
    evolution's capital reclaim, the dashboard — would then be wrong).
    """
    result = await session.execute(
        select(Position).where(Position.portfolio_id == portfolio.id)
    )
    positions = result.scalars().all()
    positions_value = money(sum((money(p.market_value) for p in positions), ZERO))
    unrealized = money(sum((money(p.unrealized_pnl) for p in positions), ZERO))
    total = money(portfolio.cash + portfolio.reserved_capital + positions_value)

    portfolio.positions_value = positions_value
    portfolio.unrealized_pnl = unrealized
    portfolio.total_value = total
    if total > portfolio.peak_value:
        portfolio.peak_value = total
    if portfolio.peak_value > 0:
        portfolio.max_drawdown = money(
            (portfolio.peak_value - total) / portfolio.peak_value
        )
    portfolio.last_valued_at = datetime.now(timezone.utc)

    account = await session.get(Account, portfolio.account_id)
    if account is not None:
        account.unrealized_pnl = unrealized
    return portfolio


async def mark_to_market(
    session: AsyncSession, portfolio: Portfolio, prices: dict[str, Decimal]
) -> Portfolio:
    """Revalue positions and recompute portfolio aggregates from marks."""
    positions_value = ZERO
    unrealized = ZERO
    for position in portfolio.positions:
        if position.quantity <= 0:
            continue
        price = money(prices.get(position.symbol, position.last_price))
        position.last_price = price
        position.market_value = position_market_value(position.quantity, price)
        position.unrealized_pnl = position_unrealized_pnl(
            position.quantity, price, position.average_cost
        )
        positions_value = money(positions_value + position.market_value)
        unrealized = money(unrealized + position.unrealized_pnl)

    total = money(portfolio.cash + portfolio.reserved_capital + positions_value)
    portfolio.positions_value = positions_value
    portfolio.unrealized_pnl = unrealized
    portfolio.total_value = total
    if total > portfolio.peak_value:
        portfolio.peak_value = total
    if portfolio.peak_value > 0:
        portfolio.max_drawdown = money(
            (portfolio.peak_value - total) / portfolio.peak_value
        )
    portfolio.last_valued_at = datetime.now(timezone.utc)

    account = await session.get(Account, portfolio.account_id)
    if account is not None:
        account.unrealized_pnl = unrealized
    return portfolio


async def snapshot(session: AsyncSession, portfolio: Portfolio) -> AccountState:
    return AccountState(
        cash=money(portfolio.cash),
        reserved_capital=money(portfolio.reserved_capital),
        positions_value=money(portfolio.positions_value),
    )


async def verify_invariants(session: AsyncSession, portfolio: Portfolio) -> list[str]:
    """Recompute the ledger from transactions and report any divergence.

    Used by tests and by the monitoring job; a non-empty result means the
    cached aggregates disagree with the immutable transaction log.
    """
    problems: list[str] = []
    result = await session.execute(
        select(Transaction).where(Transaction.portfolio_id == portfolio.id)
    )
    txns = result.scalars().all()
    net_cash = sum((t.net_amount for t in txns), ZERO)

    # Cash reconstruction: start from the account's starting capital.
    account = await session.get(Account, portfolio.account_id)
    if account is not None:
        reconstructed = money(account.starting_capital + net_cash)
        if reconstructed != money(account.cash):
            problems.append(
                f"cash mismatch: ledger {reconstructed} vs account {account.cash}"
            )

    positions_value = sum((p.market_value for p in portfolio.positions), ZERO)
    expected_total = money(portfolio.cash + portfolio.reserved_capital + positions_value)
    if expected_total != money(portfolio.total_value):
        problems.append(
            f"portfolio value mismatch: recomputed {expected_total} vs cached {portfolio.total_value}"
        )
    return problems
