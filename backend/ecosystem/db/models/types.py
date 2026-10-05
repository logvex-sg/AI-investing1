"""Shared SQL column types.

Monetary and quantity columns use fixed-precision NUMERIC rather than floats.
Floating point is never used for money: the accounting service must be exact.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

Money = Numeric(24, 8)
Quantity = Numeric(28, 12)
Ratio = Numeric(18, 10)

# Embedding dimensionality for the local hashing embedder. 384 keeps the
# pgvector index small enough for a 16 GB laptop while remaining expressive.
EMBEDDING_DIM = 384


class NumericAsDecimal(TypeDecorator):
    """Coerce values to Decimal on the way in and out."""

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 24, scale: int = 8):
        super().__init__(precision=precision, scale=scale)

    def process_bind_param(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(str(value))


JSON = JSONB
