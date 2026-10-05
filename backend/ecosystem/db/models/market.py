"""Market data series and their metadata."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from ecosystem.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from ecosystem.db.models.types import JSON


class MarketDataSeries(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A named OHLCV series (e.g. synthetic:BTCUSD:1d)."""

    __tablename__ = "market_data_series"
    __table_args__ = (
        UniqueConstraint("symbol", "interval", "source", name="uq_market_series_key"),
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    interval: Mapped[str] = mapped_column(String(8), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(24), default="crypto", nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    bar_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    volatility_regime: Mapped[str | None] = mapped_column(String(24))
    checksum: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


class MarketBar(UUIDPrimaryKeyMixin, Base):
    """A single OHLCV bar. Times are UTC and bar-close semantics apply."""

    __tablename__ = "market_bars"
    __table_args__ = (
        UniqueConstraint("series_id", "timestamp", name="uq_market_bars_series_time"),
        Index("ix_market_bars_series_timestamp", "series_id", "timestamp"),
    )

    series_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("market_data_series.id", ondelete="CASCADE"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float] = mapped_column(Float, nullable=False)
    spread_bps: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
