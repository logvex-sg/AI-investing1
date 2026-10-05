"""Market data types and deterministic synthetic generation.

The synthetic generator is seeded, so the same seed always yields the same
series. That makes every backtest in the system reproducible and lets the
whole ecosystem run offline. Real providers implement the same `MarketSource`
protocol, so nothing downstream changes when live data is plugged in.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Protocol, Sequence


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar. `timestamp` is the bar close time, in UTC.

    Bar-close semantics matter: a strategy may only see bars whose close has
    already happened, which is how look-ahead bias is prevented.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread_bps: float = 0.0

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0


@dataclass(frozen=True)
class Series:
    symbol: str
    interval: str
    bars: list[Bar]
    asset_class: str = "crypto"
    quote_currency: str = "USD"

    def __len__(self) -> int:
        return len(self.bars)

    def slice(self, start: int, end: int) -> "Series":
        return Series(self.symbol, self.interval, self.bars[start:end],
                      self.asset_class, self.quote_currency)

    @property
    def checksum(self) -> str:
        h = hashlib.sha256()
        for b in self.bars:
            h.update(f"{b.timestamp.isoformat()}:{b.close:.10f}:{b.volume:.6f}".encode())
        return h.hexdigest()


class MarketSource(Protocol):
    """Interface every market data provider must satisfy."""

    def load(self, symbol: str, interval: str, limit: int) -> Series: ...


# Per-asset parameters: annual drift, annual volatility, starting price.
# BTC is deliberately given high volatility and is treated as a volatile asset,
# never as a stable-value currency.
ASSET_PROFILES: dict[str, dict[str, float]] = {
    "EURUSD": {"drift": 0.005, "vol": 0.07, "price": 1.08, "spread_bps": 1.0, "class": "fx"},
    "USD": {"drift": 0.02, "vol": 0.005, "price": 1.0, "spread_bps": 0.5, "class": "cash"},
    "EUR": {"drift": 0.015, "vol": 0.004, "price": 1.0, "spread_bps": 0.5, "class": "cash"},
    "BTCUSD": {"drift": 0.25, "vol": 0.75, "price": 42000.0, "spread_bps": 8.0, "class": "crypto"},
    "SPX": {"drift": 0.08, "vol": 0.16, "price": 4800.0, "spread_bps": 2.0, "class": "equity"},
}


class SyntheticMarketSource:
    """Deterministic geometric-Brownian-motion generator with volatility
    clustering, so regimes and drawdowns look realistic rather than smooth."""

    def __init__(self, seed: int = 7, start: datetime | None = None):
        self.seed = seed
        self.start = start or datetime(2023, 1, 1, tzinfo=timezone.utc)

    def _rng(self, symbol: str, interval: str) -> random.Random:
        # Seed per (symbol, interval) so series are independent yet stable.
        digest = hashlib.sha256(f"{self.seed}:{symbol}:{interval}".encode()).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    def load(self, symbol: str, interval: str, limit: int) -> Series:
        profile = ASSET_PROFILES.get(symbol)
        if profile is None:
            profile = {"drift": 0.05, "vol": 0.30, "price": 100.0,
                       "spread_bps": 5.0, "class": "crypto"}
        rng = self._rng(symbol, interval)
        step = _interval_delta(interval)
        periods_per_year = timedelta(days=365) / step
        dt = 1.0 / float(periods_per_year)

        mu = profile["drift"]
        base_vol = profile["vol"]
        price = profile["price"]
        spread_bps = profile["spread_bps"]

        bars: list[Bar] = []
        ts = self.start
        # A slowly varying volatility multiplier produces calm and turbulent
        # regimes instead of uniform noise.
        vol_mult = 1.0
        for i in range(limit):
            vol_mult = max(0.35, min(3.5, vol_mult * math.exp(rng.gauss(0, 0.05))))
            sigma = base_vol * vol_mult
            shock = rng.gauss(0.0, 1.0)
            ret = (mu - 0.5 * sigma * sigma) * dt + sigma * math.sqrt(dt) * shock
            open_px = price
            close_px = max(0.0001, open_px * math.exp(ret))
            wick = abs(rng.gauss(0.0, 1.0)) * sigma * math.sqrt(dt) * open_px
            high_px = max(open_px, close_px) + wick
            low_px = max(0.0001, min(open_px, close_px) - wick)
            # Volume rises with absolute return, a common empirical regularity.
            base_vol_units = 1_000_000.0 if profile["class"] != "crypto" else 500.0
            volume = base_vol_units * (1.0 + 4.0 * abs(ret)) * (1.0 + rng.random())
            bars.append(
                Bar(
                    timestamp=ts,
                    open=round(open_px, 6),
                    high=round(high_px, 6),
                    low=round(low_px, 6),
                    close=round(close_px, 6),
                    volume=round(volume, 4),
                    spread_bps=spread_bps,
                )
            )
            price = close_px
            ts = ts + step
        return Series(symbol, interval, bars, profile.get("class", "crypto"),
                      "USD" if symbol != "EURUSD" else "USD")


def _interval_delta(interval: str) -> timedelta:
    mapping = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
        "1d": timedelta(days=1),
        "1w": timedelta(weeks=1),
    }
    if interval not in mapping:
        raise ValueError(f"unsupported interval: {interval}")
    return mapping[interval]


def series_from_rows(symbol: str, interval: str, rows: Iterable[Sequence]) -> Series:
    """Build a Series from (timestamp, open, high, low, close, volume) rows."""
    bars = [
        Bar(
            timestamp=r[0] if isinstance(r[0], datetime) else datetime.fromisoformat(str(r[0])),
            open=float(r[1]), high=float(r[2]), low=float(r[3]),
            close=float(r[4]), volume=float(r[5]),
            spread_bps=float(r[6]) if len(r) > 6 else 0.0,
        )
        for r in rows
    ]
    return Series(symbol, interval, bars)


def simple_returns(series: Series) -> list[float]:
    out: list[float] = []
    for prev, cur in zip(series.bars, series.bars[1:]):
        if prev.close > 0:
            out.append(cur.close / prev.close - 1.0)
    return out


def annualized_volatility(series: Series, periods_per_year: int = 365) -> float:
    rets = simple_returns(series)
    if len(rets) < 2:
        return 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def classify_regime(series: Series, lookback: int = 30) -> str:
    """Label the most recent window as calm / normal / turbulent."""
    window = series.bars[-lookback:]
    if len(window) < 5:
        return "unknown"
    rets = [window[i].close / window[i - 1].close - 1.0 for i in range(1, len(window))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    vol = math.sqrt(var)
    if vol < 0.01:
        return "calm"
    if vol < 0.03:
        return "normal"
    return "turbulent"
