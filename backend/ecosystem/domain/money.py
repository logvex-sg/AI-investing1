"""Decimal helpers.

Money never touches binary floating point. Every monetary value that enters
the deterministic services passes through `to_decimal` first.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

MONEY_PLACES = Decimal("0.00000001")   # 8 dp
QUANTITY_PLACES = Decimal("0.000000000001")  # 12 dp
RATIO_PLACES = Decimal("0.0000000001")  # 10 dp

ZERO = Decimal("0")


def to_decimal(value: object) -> Decimal:
    """Convert ints, strings, Decimals or floats to an exact Decimal.

    Floats are routed through their string form so that 0.1 does not become
    0.1000000000000000055511151231257827.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("bool is not a valid monetary value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(f"cannot convert {type(value).__name__} to Decimal")


def money(value: object) -> Decimal:
    return to_decimal(value).quantize(MONEY_PLACES, rounding=ROUND_HALF_EVEN)


def quantity(value: object) -> Decimal:
    return to_decimal(value).quantize(QUANTITY_PLACES, rounding=ROUND_HALF_EVEN)


def ratio(value: object) -> Decimal:
    return to_decimal(value).quantize(RATIO_PLACES, rounding=ROUND_HALF_EVEN)


def safe_div(numerator: Decimal, denominator: Decimal, default: Decimal = ZERO) -> Decimal:
    if denominator == 0:
        return default
    return numerator / denominator


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    if value < low:
        return low
    if value > high:
        return high
    return value


def is_positive(value: Decimal) -> bool:
    return value > 0


def as_float(value: Decimal) -> float:
    """Only for presentation and JSON serialisation, never for arithmetic."""
    return float(value)


def parse_percent(value: object) -> Decimal:
    """Accept '5%', 0.05 or 5 and return a fraction."""
    if isinstance(value, str) and value.strip().endswith("%"):
        return to_decimal(value.strip()[:-1]) / Decimal(100)
    return to_decimal(value)


def pct_of(base: Decimal, fraction: Decimal) -> Decimal:
    return money(base * fraction)


__all__ = [
    "MONEY_PLACES",
    "QUANTITY_PLACES",
    "RATIO_PLACES",
    "ZERO",
    "InvalidOperation",
    "as_float",
    "clamp",
    "is_positive",
    "money",
    "parse_percent",
    "pct_of",
    "quantity",
    "ratio",
    "safe_div",
    "to_decimal",
]
