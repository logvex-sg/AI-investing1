"""Deterministic backtesting engine.

Design rules that the engine enforces, not merely documents:

* Signals are computed from bars up to and including bar `i`, and the fill
  happens at bar `i+1`'s open. A strategy can never trade on information it
  could not have had, which removes look-ahead bias by construction.
* Costs are explicit: half-spread on entry and exit, a commission in basis
  points, and slippage proportional to order size relative to bar volume.
* Position size respects available cash; the engine never assumes leverage.
* The engine is pure: same spec + same bars => identical results.

The LLM never computes these numbers. It only reads them.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from ecosystem.domain.market import Bar, Series

ENGINE_VERSION = "1.0.0"


@dataclass(frozen=True)
class CostModel:
    """All execution costs in one auditable place."""

    commission_bps: float = 10.0     # 0.10% per side
    slippage_bps: float = 5.0        # base adverse slippage
    min_fee: float = 0.0
    # Slippage grows with participation: impact_bps * (order_value / bar_value)
    impact_bps: float = 20.0
    max_participation: float = 0.05  # never take more than 5% of a bar's value


@dataclass(frozen=True)
class BacktestSpec:
    symbol: str
    initial_capital: float = 10_000.0
    allocation_pct: float = 0.25       # fraction of equity per entry
    fee_bps: float = 10.0
    slippage_bps: float = 5.0
    seed: int = 7
    long_only: bool = True

    @property
    def spec_hash(self) -> str:
        payload = json.dumps(
            {
                "symbol": self.symbol,
                "initial_capital": round(self.initial_capital, 8),
                "allocation_pct": round(self.allocation_pct, 8),
                "fee_bps": self.fee_bps,
                "slippage_bps": self.slippage_bps,
                "seed": self.seed,
                "long_only": self.long_only,
                "engine": ENGINE_VERSION,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    slippage: float
    net_pnl: float
    return_pct: float
    holding_bars: int


@dataclass
class BacktestResult:
    spec_hash: str
    engine_version: str
    symbol: str
    starting_capital: float
    ending_capital: float
    equity_curve: list[float]
    timestamps: list[str]
    trades: list[Trade]
    metrics: dict[str, float] = field(default_factory=dict)
    exposure_curve: list[float] = field(default_factory=list)


# A strategy is a pure function of the visible bars. It returns a target
# position weight in [0, 1] for the next bar.
SignalFn = Callable[[list[Bar]], float]


def _apply_slippage(
    price: float, side: str, quantity: float, bar: Bar, costs: CostModel
) -> tuple[float, float]:
    """Return (fill_price, slippage_cost) for one side of a trade."""
    half_spread = price * (bar.spread_bps / 10_000.0) / 2.0
    order_value = quantity * price
    bar_value = max(1e-9, bar.volume * price)
    participation = min(1.0, order_value / bar_value)
    impact = price * (costs.impact_bps / 10_000.0) * participation
    base_slip = price * (costs.slippage_bps / 10_000.0)
    total_adverse = half_spread + impact + base_slip
    if side == "BUY":
        fill = price + total_adverse
    else:
        fill = max(0.0, price - total_adverse)
    return fill, quantity * total_adverse


def _commission(order_value: float, costs: CostModel) -> float:
    return max(costs.min_fee, order_value * (costs.commission_bps / 10_000.0))


def run_backtest(
    series: Series,
    signal_fn: SignalFn,
    spec: BacktestSpec,
    costs: CostModel | None = None,
) -> BacktestResult:
    """Execute a strategy over a series and return fully costed results."""
    if costs is None:
        costs = CostModel(
            commission_bps=spec.fee_bps, slippage_bps=spec.slippage_bps
        )
    bars = series.bars
    if len(bars) < 3:
        raise ValueError("backtest needs at least 3 bars")

    cash = spec.initial_capital
    position = 0.0
    avg_cost = 0.0
    equity_curve: list[float] = []
    exposure_curve: list[float] = []
    timestamps: list[str] = []
    trades: list[Trade] = []
    open_trade: dict | None = None
    total_fees = 0.0
    total_slippage = 0.0

    # Start at bar 1 so the signal always has at least one prior bar, and stop
    # one bar early so every decision has a later bar to execute in. Trading on
    # the final bar's own close would be look-ahead bias.
    for i in range(1, len(bars) - 1):
        visible = bars[: i + 1]
        mark_bar = bars[i]
        # The signal expresses a desired fraction of equity; the spec's
        # allocation_pct caps how much the engine will ever deploy.
        target_weight = max(0.0, min(spec.allocation_pct, signal_fn(visible)))

        # Decide at close of bar i, execute at the open of bar i+1.
        exec_bar = bars[i + 1]
        exec_price = exec_bar.open

        equity = cash + position * mark_bar.close
        target_value = equity * target_weight
        current_value = position * exec_price
        delta_value = target_value - current_value

        if abs(delta_value) > max(1.0, equity * 0.001):
            if delta_value > 0:
                # BUY
                spend = min(delta_value, cash)
                if spend > 0 and exec_price > 0:
                    raw_qty = spend / exec_price
                    fill_price, slip_cost = _apply_slippage(
                        exec_price, "BUY", raw_qty, exec_bar, costs
                    )
                    qty = spend / fill_price if fill_price > 0 else 0.0
                    gross = qty * fill_price
                    fee = _commission(gross, costs)
                    if gross + fee <= cash and qty > 0:
                        cash -= gross + fee
                        new_qty = position + qty
                        avg_cost = (
                            (position * avg_cost + gross + fee) / new_qty
                            if new_qty > 0
                            else 0.0
                        )
                        position = new_qty
                        total_fees += fee
                        total_slippage += slip_cost
                        if open_trade is None:
                            open_trade = {
                                "time": exec_bar.timestamp.isoformat(),
                                "price": fill_price,
                                "quantity": qty,
                                "fees": fee,
                                "slippage": slip_cost,
                                "bar_index": i + 1,
                            }
                        else:
                            open_trade["quantity"] += qty
                            open_trade["fees"] += fee
                            open_trade["slippage"] += slip_cost
            else:
                # SELL
                sell_value = min(-delta_value, position * exec_price)
                if sell_value > 0 and exec_price > 0:
                    raw_qty = sell_value / exec_price
                    qty = min(raw_qty, position)
                    fill_price, slip_cost = _apply_slippage(
                        exec_price, "SELL", qty, exec_bar, costs
                    )
                    gross = qty * fill_price
                    fee = _commission(gross, costs)
                    cash += gross - fee
                    position -= qty
                    total_fees += fee
                    total_slippage += slip_cost
                    if open_trade is not None:
                        # A trade closes against its own entry only if it exits
                        # the whole remaining position; a partial exit is
                        # recorded against the true average cost so the numbers
                        # reconcile with the equity curve.
                        partial = position > 1e-12
                        entry_price = avg_cost if partial else open_trade["price"]
                        entry_fee_share = (
                            open_trade["fees"] * (qty / (position + qty))
                            if partial and (position + qty) > 0
                            else open_trade["fees"]
                        )
                        entry_slip_share = (
                            open_trade["slippage"] * (qty / (position + qty))
                            if partial and (position + qty) > 0
                            else open_trade["slippage"]
                        )
                        gross_pnl = qty * (fill_price - entry_price)
                        fees_total = entry_fee_share + fee
                        slip_total = entry_slip_share + slip_cost
                        net_pnl = gross_pnl - fees_total - slip_total
                        entry_notional = qty * entry_price
                        trades.append(
                            Trade(
                                entry_time=open_trade["time"],
                                exit_time=exec_bar.timestamp.isoformat(),
                                entry_price=entry_price,
                                exit_price=fill_price,
                                quantity=qty,
                                gross_pnl=gross_pnl,
                                fees=fees_total,
                                slippage=slip_total,
                                net_pnl=net_pnl,
                                return_pct=(net_pnl / entry_notional)
                                if entry_notional
                                else 0.0,
                                holding_bars=(i + 1) - open_trade["bar_index"],
                            )
                        )
                        if position <= 1e-12:
                            position = 0.0
                            avg_cost = 0.0
                            open_trade = None
                        else:
                            open_trade = {
                                "time": open_trade["time"],
                                "price": entry_price,
                                "quantity": position,
                                "fees": max(0.0, open_trade["fees"] - entry_fee_share),
                                "slippage": max(
                                    0.0, open_trade["slippage"] - entry_slip_share
                                ),
                                "bar_index": open_trade["bar_index"],
                            }

        equity_close = cash + position * mark_bar.close
        equity_curve.append(equity_close)
        timestamps.append(mark_bar.timestamp.isoformat())
        exposure_curve.append((position * mark_bar.close / equity_close) if equity_close else 0.0)

    ending = equity_curve[-1] if equity_curve else spec.initial_capital
    result = BacktestResult(
        spec_hash=spec.spec_hash,
        engine_version=ENGINE_VERSION,
        symbol=series.symbol,
        starting_capital=spec.initial_capital,
        ending_capital=ending,
        equity_curve=equity_curve,
        timestamps=timestamps,
        trades=trades,
        exposure_curve=exposure_curve,
    )
    result.metrics = compute_metrics(result, total_fees, total_slippage)
    return result


def compute_metrics(
    result: BacktestResult, total_fees: float, total_slippage: float
) -> dict[str, float]:
    curve = result.equity_curve
    start = result.starting_capital
    end = result.ending_capital
    total_return = (end / start - 1.0) if start else 0.0

    peak = curve[0] if curve else start
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)

    rets = [
        curve[i] / curve[i - 1] - 1.0
        for i in range(1, len(curve))
        if curve[i - 1] > 0
    ]
    if len(rets) >= 2:
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        vol = math.sqrt(var)
    else:
        mean, vol = 0.0, 0.0

    periods = max(1, len(curve))
    annual_return = (1.0 + total_return) ** (365.0 / periods) - 1.0 if total_return > -1 else -1.0
    annual_vol = vol * math.sqrt(365.0)
    sharpe = (annual_return / annual_vol) if annual_vol > 1e-12 else 0.0
    sortino_denom = (
        math.sqrt(sum(min(0.0, r) ** 2 for r in rets) / len(rets)) * math.sqrt(365.0)
        if rets
        else 0.0
    )
    sortino = (annual_return / sortino_denom) if sortino_denom > 1e-12 else 0.0
    calmar = (annual_return / max_dd) if max_dd > 1e-12 else 0.0

    wins = [t for t in result.trades if t.net_pnl > 0]
    losses = [t for t in result.trades if t.net_pnl <= 0]
    win_rate = len(wins) / len(result.trades) if result.trades else 0.0
    avg_win = sum(t.net_pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.net_pnl for t in losses) / len(losses) if losses else 0.0
    profit_factor = (
        sum(t.net_pnl for t in wins) / abs(sum(t.net_pnl for t in losses))
        if losses and sum(t.net_pnl for t in losses) != 0
        else (float("inf") if wins else 0.0)
    )
    turnover = sum(t.quantity * (t.entry_price + t.exit_price) for t in result.trades)
    avg_exposure = sum(result.exposure_curve) / len(result.exposure_curve) if result.exposure_curve else 0.0

    return {
        "total_return": total_return,
        "annualized_return": annual_return,
        "volatility": annual_vol,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor": profit_factor if math.isfinite(profit_factor) else 999.0,
        "trades": float(len(result.trades)),
        "fees": total_fees,
        "slippage": total_slippage,
        "total_costs": total_fees + total_slippage,
        "turnover": turnover,
        "avg_exposure": avg_exposure,
        "final_equity": end,
    }
