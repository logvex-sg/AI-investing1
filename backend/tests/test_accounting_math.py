"""Accounting arithmetic must be exact and reproducible."""

from decimal import Decimal

import pytest

from ecosystem.domain.accounting_math import (
    AccountState,
    PositionState,
    allocation_fraction,
    available_capital,
    buy_cost,
    concentration_ratio,
    drawdown_fraction,
    max_drawdown,
    money,
    new_average_cost,
    portfolio_value,
    position_unrealized_pnl,
    quantize_qty,
    realized_pnl_on_sell,
    sell_proceeds,
    split_capital,
    total_return,
)
from ecosystem.domain.money import to_decimal


def D(x) -> Decimal:
    return to_decimal(x)


def test_buy_cost_includes_fee():
    assert buy_cost(D("2"), D("100"), D("1.5")) == D("201.5")


def test_sell_proceeds_subtracts_fee():
    assert sell_proceeds(D("2"), D("100"), D("1.5")) == D("198.5")


def test_weighted_average_cost_capitalises_fee():
    # 1 unit at 100 (no fee), then 1 unit at 200 with a 10 fee.
    avg = new_average_cost(D("1"), D("100"), D("1"), D("200"), D("10"))
    # (100 + 200 + 10) / 2 = 155
    assert avg == D("155")


def test_average_cost_of_empty_position_is_zero():
    assert new_average_cost(D("0"), D("0"), D("0"), D("0"), D("0")) == D("0")


def test_realized_pnl_is_net_of_fees():
    pnl = realized_pnl_on_sell(D("1"), D("150"), D("100"), D("2"))
    assert pnl == D("48")  # (150-100)*1 - 2


def test_realized_loss():
    pnl = realized_pnl_on_sell(D("1"), D("90"), D("100"), D("1"))
    assert pnl == D("-11")


def test_position_market_value_and_unrealized():
    pos = PositionState("BTCUSD", D("0.5"), D("40000"), D("44000"))
    assert pos.market_value == D("22000")
    assert pos.unrealized_pnl == D("2000")
    assert pos.cost_basis == D("20000")


def test_unrealized_pnl_negative():
    assert position_unrealized_pnl(D("0.5"), D("36000"), D("40000")) == D("-2000")


def test_portfolio_value_sums_components():
    assert portfolio_value(D("1000"), D("500"), D("2500")) == D("4000")


def test_drawdown_fraction():
    assert drawdown_fraction(D("100"), D("80")) == D("0.2")
    assert drawdown_fraction(D("0"), D("80")) == D("0")


def test_max_drawdown_picks_worst_decline():
    curve = [D("100"), D("120"), D("90"), D("130"), D("110")]
    # peak 120 -> 90 = 25%; peak 130 -> 110 = 15.38%
    assert max_drawdown(curve) == D("0.25")


def test_max_drawdown_of_monotonic_curve_is_zero():
    assert max_drawdown([D("100"), D("110"), D("120")]) == D("0")


def test_total_return():
    assert total_return(D("10000"), D("12500")) == D("0.25")


def test_allocation_fraction():
    assert allocation_fraction(D("250"), D("1000")) == D("0.25")


def test_concentration_ratio():
    assert concentration_ratio([D("50"), D("30"), D("20")]) == D("0.5")
    assert concentration_ratio([]) == D("0")


def test_available_capital_excludes_reserved():
    assert available_capital(D("1000"), D("400")) == D("600")


def test_split_capital_parts_sum_to_whole():
    total = D("10000")
    parts = split_capital(
        total,
        {"protected": D("0.4"), "trading": D("0.4"),
         "operating": D("0.15"), "profit": D("0.05")},
    )
    assert sum(parts.values()) == total
    assert parts["protected"] == D("4000")
    assert parts["profit"] == D("500")


def test_split_capital_absorbs_rounding_remainder():
    total = D("100")
    parts = split_capital(total, {"a": D("0.333333333"), "b": D("0.333333333"), "c": D("0.333333334")})
    assert sum(parts.values()) == total


def test_quantize_qty_rounds_down_to_step():
    assert quantize_qty(D("1.23456"), D("0.01")) == D("1.23")
    assert quantize_qty(D("0.000000019"), D("0.00000001")) == D("0.00000001")


def test_account_state_total_value():
    state = AccountState(cash=D("1000"), reserved_capital=D("500"), positions_value=D("2500"))
    assert state.total_value == D("4000")


def test_no_float_rounding_leak():
    # Monetary functions return 8dp-quantised values, so 0.1 + 0.2 is exactly
    # 0.3 after quantisation (binary floats would give 0.30000000000000004).
    assert buy_cost(D("1"), D("0.1"), D("0")) == D("0.1")
    assert money(D("0.1") + D("0.2")) == D("0.3")
    assert to_decimal(0.1) + to_decimal(0.2) == D("0.3")


@pytest.mark.parametrize("value", [0, 1, "2.5", Decimal("3.5"), 4.0])
def test_to_decimal_accepts_numeric_types(value):
    assert isinstance(to_decimal(value), Decimal)


def test_to_decimal_rejects_bool():
    with pytest.raises(TypeError):
        to_decimal(True)
