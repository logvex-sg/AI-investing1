"""Backtester determinism, cost realism and look-ahead safety."""

import math

import pytest

from ecosystem.domain.backtest import BacktestSpec, CostModel, run_backtest
from ecosystem.domain.market import SyntheticMarketSource, classify_regime


@pytest.fixture(scope="module")
def btc_series():
    return SyntheticMarketSource(seed=7).load("BTCUSD", "1d", 400)


def always_long(visible):
    return 1.0


def always_flat(visible):
    return 0.0


def momentum_signal(visible):
    closes = [b.close for b in visible]
    if len(closes) < 21:
        return 0.0
    return 1.0 if closes[-1] > closes[-21] else 0.0


def test_synthetic_series_is_deterministic():
    a = SyntheticMarketSource(seed=7).load("BTCUSD", "1d", 100)
    b = SyntheticMarketSource(seed=7).load("BTCUSD", "1d", 100)
    assert a.checksum == b.checksum


def test_different_seeds_differ():
    a = SyntheticMarketSource(seed=1).load("BTCUSD", "1d", 100)
    b = SyntheticMarketSource(seed=2).load("BTCUSD", "1d", 100)
    assert a.checksum != b.checksum


def test_btc_is_volatile_not_stable():
    series = SyntheticMarketSource(seed=7).load("BTCUSD", "1d", 400)
    eur = SyntheticMarketSource(seed=7).load("EUR", "1d", 400)
    from ecosystem.domain.market import annualized_volatility

    assert annualized_volatility(series) > annualized_volatility(eur) * 5


def test_backtest_is_deterministic(btc_series):
    spec = BacktestSpec(symbol="BTCUSD", seed=7)
    r1 = run_backtest(btc_series, momentum_signal, spec)
    r2 = run_backtest(btc_series, momentum_signal, spec)
    assert r1.equity_curve == r2.equity_curve
    assert r1.metrics == r2.metrics
    assert r1.spec_hash == r2.spec_hash


def test_flat_strategy_preserves_capital(btc_series):
    spec = BacktestSpec(symbol="BTCUSD")
    result = run_backtest(btc_series, always_flat, spec)
    assert result.ending_capital == pytest.approx(spec.initial_capital)
    assert result.metrics["trades"] == 0
    assert result.metrics["total_costs"] == 0


def test_costs_are_charged(btc_series):
    spec = BacktestSpec(symbol="BTCUSD", fee_bps=10, slippage_bps=5)
    result = run_backtest(btc_series, momentum_signal, spec)
    assert result.metrics["fees"] > 0
    assert result.metrics["slippage"] > 0
    assert result.metrics["total_costs"] == pytest.approx(
        result.metrics["fees"] + result.metrics["slippage"]
    )


def test_higher_fees_reduce_returns(btc_series):
    cheap = run_backtest(btc_series, momentum_signal, BacktestSpec(symbol="BTCUSD", fee_bps=1))
    pricey = run_backtest(btc_series, momentum_signal, BacktestSpec(symbol="BTCUSD", fee_bps=50))
    assert pricey.metrics["total_return"] <= cheap.metrics["total_return"]


def test_equity_curve_length_matches_tradeable_bars(btc_series):
    result = run_backtest(btc_series, momentum_signal, BacktestSpec(symbol="BTCUSD"))
    # One bar of history is needed to decide, one bar is reserved to execute.
    assert len(result.equity_curve) == len(btc_series.bars) - 2


def test_never_goes_negative_or_leveraged(btc_series):
    result = run_backtest(btc_series, always_long, BacktestSpec(symbol="BTCUSD", allocation_pct=0.25))
    assert all(v > 0 for v in result.equity_curve)
    # Exposure is measured at the close, after the fill happened at the open,
    # so intra-bar price movement can push it slightly past the allocation.
    assert all(0.0 <= e <= 0.35 for e in result.exposure_curve)


def test_no_look_ahead_signal_only_sees_past():
    """A signal that peeked at the *next* bar would earn impossible returns.
    The engine must pass only bars up to the decision point."""
    seen_lengths: list[int] = []

    def spy(visible):
        seen_lengths.append(len(visible))
        return 0.0

    series = SyntheticMarketSource(seed=3).load("BTCUSD", "1d", 20)
    run_backtest(series, spy, BacktestSpec(symbol="BTCUSD"))
    # The first decision sees two bars; the last sees every bar but the final
    # one, which is reserved for execution.
    assert seen_lengths[0] == 2
    assert seen_lengths[-1] == len(series.bars) - 1
    assert seen_lengths == sorted(seen_lengths)


def test_allocation_pct_caps_exposure(btc_series):
    result = run_backtest(
        btc_series, always_long, BacktestSpec(symbol="BTCUSD", allocation_pct=0.1)
    )
    assert max(result.exposure_curve) <= 0.2


def test_metrics_are_finite(btc_series):
    result = run_backtest(btc_series, momentum_signal, BacktestSpec(symbol="BTCUSD"))
    for key, value in result.metrics.items():
        assert math.isfinite(value), f"{key} is not finite"


def test_trades_have_consistent_accounting(btc_series):
    result = run_backtest(btc_series, momentum_signal, BacktestSpec(symbol="BTCUSD"))
    for t in result.trades:
        expected = t.gross_pnl - t.fees - t.slippage
        assert t.net_pnl == pytest.approx(expected, abs=1e-6)


def test_max_drawdown_within_unit_interval(btc_series):
    result = run_backtest(btc_series, momentum_signal, BacktestSpec(symbol="BTCUSD"))
    assert 0.0 <= result.metrics["max_drawdown"] <= 1.0


def test_regime_classification():
    series = SyntheticMarketSource(seed=7).load("BTCUSD", "1d", 100)
    assert classify_regime(series) in {"calm", "normal", "turbulent"}


def test_too_few_bars_rejected():
    series = SyntheticMarketSource(seed=1).load("BTCUSD", "1d", 2)
    with pytest.raises(ValueError):
        run_backtest(series, momentum_signal, BacktestSpec(symbol="BTCUSD"))


def test_impact_cost_grows_with_size():
    """A larger order relative to bar volume pays more slippage."""
    from ecosystem.domain.market import Bar, Series
    from datetime import datetime, timezone, timedelta

    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    bars = [
        Bar(start + timedelta(days=i), 100.0, 101.0, 99.0, 100.0, 1_000.0, 5.0)
        for i in range(10)
    ]
    series = Series("TEST", "1d", bars)
    small = run_backtest(series, always_long, BacktestSpec(symbol="TEST", initial_capital=100.0))
    large = run_backtest(series, always_long, BacktestSpec(symbol="TEST", initial_capital=10_000.0))
    assert large.metrics["slippage"] > small.metrics["slippage"]
