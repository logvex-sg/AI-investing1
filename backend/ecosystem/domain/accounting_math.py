"""Pure accounting mathematics.

These functions contain no database access and no randomness, so they can be
tested exhaustively and reused by the live accounting service and the
backtester alike. This is the single definition of how money moves.

Conventions
-----------
* Fees are capitalised into the cost basis on a buy, and deducted from
  proceeds on a sell. This keeps `realized_pnl` net of costs.
* `realized_pnl` is only ever produced by a sell.
* `unrealized_pnl` is always recomputed from the current mark, never accrued.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ecosystem.domain.money import ZERO, money, quantity, safe_div


@dataclass(frozen=True)
class PositionState:
    """Immutable snapshot of a single position."""

    symbol: str
    quantity: Decimal
    average_cost: Decimal
    last_price: Decimal

    @property
    def market_value(self) -> Decimal:
        return money(self.quantity * self.last_price)

    @property
    def cost_basis(self) -> Decimal:
        return money(self.quantity * self.average_cost)

    @property
    def unrealized_pnl(self) -> Decimal:
        return money(self.quantity * (self.last_price - self.average_cost))


@dataclass(frozen=True)
class AccountState:
    """Immutable snapshot of account equity."""

    cash: Decimal
    reserved_capital: Decimal
    positions_value: Decimal

    @property
    def total_value(self) -> Decimal:
        return money(self.cash + self.reserved_capital + self.positions_value)


def buy_cost(qty: Decimal, price: Decimal, fee: Decimal) -> Decimal:
    """Total cash leaving the account to open `qty` at `price`."""
    return money(qty * price + fee)


def sell_proceeds(qty: Decimal, price: Decimal, fee: Decimal) -> Decimal:
    """Total cash entering the account to close `qty` at `price`."""
    return money(qty * price - fee)


def new_average_cost(
    old_qty: Decimal,
    old_average_cost: Decimal,
    buy_qty: Decimal,
    buy_price: Decimal,
    fee: Decimal,
) -> Decimal:
    """Weighted-average cost after a buy, with the fee capitalised."""
    new_qty = old_qty + buy_qty
    if new_qty <= 0:
        return ZERO
    total_cost = old_qty * old_average_cost + buy_qty * buy_price + fee
    return money(safe_div(total_cost, new_qty))


def realized_pnl_on_sell(
    qty: Decimal, price: Decimal, average_cost: Decimal, fee: Decimal
) -> Decimal:
    """Profit actually banked by a sell, net of fees."""
    return money(qty * (price - average_cost) - fee)


def position_market_value(qty: Decimal, last_price: Decimal) -> Decimal:
    return money(qty * last_price)


def position_unrealized_pnl(
    qty: Decimal, last_price: Decimal, average_cost: Decimal
) -> Decimal:
    return money(qty * (last_price - average_cost))


def portfolio_value(cash: Decimal, reserved: Decimal, positions_value: Decimal) -> Decimal:
    return money(cash + reserved + positions_value)


def drawdown_fraction(peak: Decimal, current: Decimal) -> Decimal:
    """Drawdown as a positive fraction of the peak (0.2 == 20% below peak)."""
    if peak <= 0:
        return ZERO
    return money((peak - current) / peak)


def max_drawdown(equity_curve: list[Decimal]) -> Decimal:
    """Largest peak-to-trough decline in a series, as a positive fraction."""
    if not equity_curve:
        return ZERO
    peak = equity_curve[0]
    worst = ZERO
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = drawdown_fraction(peak, value)
        if dd > worst:
            worst = dd
    return worst


def total_return(starting: Decimal, current: Decimal) -> Decimal:
    return money(safe_div(current - starting, starting))


def allocation_fraction(position_value: Decimal, total_value: Decimal) -> Decimal:
    return money(safe_div(position_value, total_value))


def concentration_ratio(position_values: list[Decimal]) -> Decimal:
    """Largest single position as a fraction of gross exposure (Herfindahl-free,
    deliberately simple and explainable)."""
    total = sum(position_values, ZERO)
    if total <= 0:
        return ZERO
    return money(safe_div(max(position_values), total))


def exposure_fraction(positions_value: Decimal, total_value: Decimal) -> Decimal:
    return money(safe_div(positions_value, total_value))


def available_capital(cash: Decimal, reserved: Decimal) -> Decimal:
    """Cash that may be deployed; reserved capital is untouchable."""
    return money(cash - reserved)


def split_capital(total: Decimal, splits: dict[str, Decimal]) -> dict[str, Decimal]:
    """Split capital across buckets, giving any rounding remainder to the
    largest bucket so the parts always sum exactly to the whole."""
    parts: dict[str, Decimal] = {}
    for name, fraction in splits.items():
        parts[name] = money(total * fraction)
    remainder = money(total - sum(parts.values(), ZERO))
    if remainder != 0 and parts:
        largest = max(parts, key=lambda k: parts[k])
        parts[largest] = money(parts[largest] + remainder)
    return parts


def quantize_qty(value: Decimal, step: Decimal) -> Decimal:
    """Round a quantity down to a tradable step (e.g. 0.00000001 BTC)."""
    if step <= 0:
        return quantity(value)
    steps = (value / step).to_integral_value(rounding="ROUND_FLOOR")
    return quantity(steps * step)
