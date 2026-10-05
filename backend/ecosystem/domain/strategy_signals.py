"""Deterministic strategy interpreter.

A strategy version stores structured rules. This module turns those rules into
a pure signal function. The LLM proposes rules; this code decides what they
mean. That separation is what stops a model from inventing performance.

Supported rule vocabulary (deliberately small and explainable):

Indicators
    sma(period)        simple moving average of close
    ema(period)        exponential moving average of close
    rsi(period)        relative strength index, 0..100
    momentum(period)   close / close[-period] - 1
    volatility(period) annualised stdev of returns

Conditions (entry_rules / exit_rules), each a list of dicts:
    {"indicator": "momentum", "period": 20, "op": ">", "value": 0.0}
    {"indicator": "sma", "period": 20, "op": "price_above"}
    {"indicator": "rsi", "period": 14, "op": "<", "value": 30}
"""

from __future__ import annotations

import math
from typing import Any, Callable

from ecosystem.domain.market import Bar

SUPPORTED_OPS = {">", "<", ">=", "<=", "price_above", "price_below", "crosses_above", "crosses_below"}


def sma(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(len(values) - period, len(values)):
        change = values[i] - values[i - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def momentum(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    past = values[-period - 1]
    if past == 0:
        return None
    return values[-1] / past - 1.0


def volatility(values: list[float], period: int) -> float | None:
    if len(values) < period + 1:
        return None
    rets = [
        values[i] / values[i - 1] - 1.0
        for i in range(len(values) - period, len(values))
        if values[i - 1] > 0
    ]
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(365.0)


INDICATORS: dict[str, Callable[[list[float], int], float | None]] = {
    "sma": sma,
    "ema": ema,
    "rsi": rsi,
    "momentum": momentum,
    "volatility": volatility,
}


def _evaluate_condition(rule: dict[str, Any], closes: list[float]) -> bool | None:
    name = rule.get("indicator")
    if name not in INDICATORS:
        return None
    period = int(rule.get("period", 14))
    op = rule.get("op", ">")
    if op not in SUPPORTED_OPS:
        return None
    if name == "sma" and op in {"price_above", "price_below", "crosses_above", "crosses_below"}:
        current = sma(closes, period)
        previous = sma(closes[:-1], period)
        if current is None or previous is None:
            return None
        price = closes[-1]
        prev_price = closes[-2]
        if op == "price_above":
            return price > current
        if op == "price_below":
            return price < current
        if op == "crosses_above":
            return prev_price <= previous and price > current
        return prev_price >= previous and price < current

    value = INDICATORS[name](closes, period)
    if value is None:
        return None
    threshold = float(rule.get("value", 0.0))
    return {
        ">": value > threshold,
        "<": value < threshold,
        ">=": value >= threshold,
        "<=": value <= threshold,
    }.get(op)


def conditions_met(rules: list[dict[str, Any]], closes: list[float]) -> bool:
    """All rules must hold (AND semantics). Unknown rules are ignored so an
    unrecognised proposal degrades to 'no signal' rather than a false trade."""
    if not rules:
        return False
    results = [_evaluate_condition(r, closes) for r in rules]
    known = [r for r in results if r is not None]
    if not known:
        return False
    return all(known)


def build_signal_fn(entry_rules: list[dict], exit_rules: list[dict]) -> Callable[[list[Bar]], float]:
    """Return a signal function returning a target equity fraction.

    Returns 1.0 when entry conditions hold and no exit condition holds, and
    0.0 otherwise. The backtester clamps this to the spec's allocation.
    """
    def signal(visible: list[Bar]) -> float:
        closes = [b.close for b in visible]
        if conditions_met(exit_rules, closes):
            return 0.0
        if conditions_met(entry_rules, closes):
            return 1.0
        return 0.0

    return signal


def describe_strategy(parameters: dict, entry_rules: list[dict], exit_rules: list[dict]) -> str:
    """Human-readable one-liner used in the UI."""
    def fmt(rules: list[dict]) -> str:
        parts = []
        for r in rules:
            parts.append(
                f"{r.get('indicator')}({r.get('period')}) {r.get('op')} "
                f"{r.get('value', '')}".strip()
            )
        return " AND ".join(parts) if parts else "none"

    return f"Enter when {fmt(entry_rules)}; exit when {fmt(exit_rules)}."
