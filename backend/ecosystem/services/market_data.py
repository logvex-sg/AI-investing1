"""Market data service.

Loads series from a `MarketSource`, persists them with a checksum so a
backtest can be tied to the exact bars it ran on, and exposes the latest mark
used for valuation and paper trading.

The default source is deterministic and offline. A live provider only needs to
implement `MarketSource` to replace it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ecosystem.db.models.enums import EventCategory
from ecosystem.db.models.market import MarketBar, MarketDataSeries
from ecosystem.domain.market import (
    ASSET_PROFILES,
    MarketSource,
    Series,
    SyntheticMarketSource,
    annualized_volatility,
    classify_regime,
)
from ecosystem.services import events

_source: MarketSource = SyntheticMarketSource(seed=7)


def set_source(source: MarketSource) -> None:
    """Swap in a different provider (tests, or a live feed)."""
    global _source
    _source = source


def get_source() -> MarketSource:
    return _source


async def get_or_load_series(
    session: AsyncSession,
    symbol: str,
    interval: str = "1d",
    limit: int = 500,
    *,
    source: MarketSource | None = None,
) -> tuple[MarketDataSeries, Series]:
    """Return a persisted series and its in-memory bars, generating on demand."""
    src = source or _source
    series = src.load(symbol, interval, limit)

    result = await session.execute(
        select(MarketDataSeries).where(
            MarketDataSeries.symbol == symbol, MarketDataSeries.interval == interval
        )
    )
    meta = result.scalar_one_or_none()
    if meta is not None and meta.checksum == series.checksum and meta.bar_count == len(series):
        stored = await _load_bars(session, meta.id)
        if stored:
            return meta, Series(symbol, interval, stored, meta.asset_class, meta.quote_currency)

    if meta is None:
        meta = MarketDataSeries(
            symbol=symbol,
            interval=interval,
            source=type(src).__name__,
            asset_class=series.asset_class,
            quote_currency=series.quote_currency,
        )
        session.add(meta)
        await session.flush()

    # Replace the bars atomically for this series.
    existing = await session.execute(
        select(MarketBar).where(MarketBar.series_id == meta.id)
    )
    for bar in existing.scalars().all():
        await session.delete(bar)
    await session.flush()

    for bar in series.bars:
        session.add(
            MarketBar(
                series_id=meta.id,
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                spread_bps=bar.spread_bps,
            )
        )

    meta.bar_count = len(series.bars)
    meta.start_time = series.bars[0].timestamp if series.bars else None
    meta.end_time = series.bars[-1].timestamp if series.bars else None
    meta.volatility_regime = classify_regime(series)
    meta.checksum = series.checksum
    meta.extra = {"annualized_volatility": round(annualized_volatility(series), 6)}
    await session.flush()

    await events.emit(
        session,
        EventCategory.RESEARCH,
        "market_data_loaded",
        f"Loaded {len(series.bars)} {interval} bars for {symbol} "
        f"({meta.volatility_regime} regime).",
        source="market_data",
        payload={"symbol": symbol, "interval": interval, "checksum": meta.checksum},
    )
    return meta, series


async def _load_bars(session: AsyncSession, series_id: uuid.UUID) -> list:
    from ecosystem.domain.market import Bar

    result = await session.execute(
        select(MarketBar).where(MarketBar.series_id == series_id).order_by(MarketBar.timestamp)
    )
    return [
        Bar(
            timestamp=row.timestamp,
            open=row.open,
            high=row.high,
            low=row.low,
            close=row.close,
            volume=row.volume,
            spread_bps=row.spread_bps,
        )
        for row in result.scalars().all()
    ]


async def latest_price(
    session: AsyncSession, symbol: str, interval: str = "1d"
) -> tuple[float, str]:
    """Return the most recent close and the asset class for a symbol."""
    _, series = await get_or_load_series(session, symbol, interval)
    if not series.bars:
        raise ValueError(f"no market data for {symbol}")
    return series.bars[-1].close, series.asset_class


async def latest_prices(
    session: AsyncSession, symbols: list[str], interval: str = "1d"
) -> dict[str, float]:
    prices: dict[str, float] = {}
    for symbol in symbols:
        try:
            price, _ = await latest_price(session, symbol, interval)
            prices[symbol] = price
        except ValueError:
            continue
    return prices


def supported_symbols() -> list[str]:
    return sorted(ASSET_PROFILES.keys())


def is_volatile(symbol: str) -> bool:
    """BTC and other crypto are volatile; they are never treated as cash."""
    profile = ASSET_PROFILES.get(symbol)
    if profile is None:
        return True
    return profile.get("class") in ("crypto",)
