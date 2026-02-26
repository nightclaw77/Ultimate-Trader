"""
Real-time crypto price feed using Binance public REST API (no API key needed).
Provides momentum signals for auto-trading decisions.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BINANCE_BASE = "https://api.binance.com/api/v3"

SYMBOL_MAP = {
    "bitcoin":  "BTCUSDT",
    "btc":      "BTCUSDT",
    "ethereum": "ETHUSDT",
    "eth":      "ETHUSDT",
    "solana":   "SOLUSDT",
    "sol":      "SOLUSDT",
    "xrp":      "XRPUSDT",
}


def _market_to_symbol(market_question: str) -> Optional[str]:
    q = market_question.lower()
    for keyword, symbol in SYMBOL_MAP.items():
        if keyword in q:
            return symbol
    return None


class PriceFeed:
    """Fetches live prices and computes momentum signals from Binance."""

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        # Cache: symbol -> (timestamp, klines)
        self._cache: dict = {}
        self._cache_ttl = 30  # seconds

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                headers={"Accept-Encoding": "gzip, deflate"},
            )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def get_price(self, symbol: str) -> float:
        """Current ticker price."""
        await self._ensure_session()
        try:
            async with self._session.get(
                f"{BINANCE_BASE}/ticker/price",
                params={"symbol": symbol},
            ) as r:
                if r.status == 200:
                    d = await r.json()
                    return float(d["price"])
        except Exception as e:
            logger.warning(f"[PriceFeed] get_price {symbol} failed: {e}")
        return 0.0

    async def get_klines(self, symbol: str, interval: str = "5m", limit: int = 12) -> list:
        """
        Returns list of klines: [open_time, open, high, low, close, volume, ...]
        Uses 30s cache to avoid spamming Binance.
        """
        cache_key = f"{symbol}_{interval}"
        now = datetime.now(timezone.utc).timestamp()
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return data

        await self._ensure_session()
        try:
            async with self._session.get(
                f"{BINANCE_BASE}/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
            ) as r:
                if r.status == 200:
                    data = await r.json()
                    self._cache[cache_key] = (now, data)
                    return data
        except Exception as e:
            logger.warning(f"[PriceFeed] klines {symbol}/{interval} failed: {e}")
        return []

    async def get_signal(self, symbol: str) -> dict:
        """
        Compute momentum signal for a symbol.

        Returns:
          {
            "symbol": "BTCUSDT",
            "price": 84230.5,
            "change_1m": 0.12,    # % change last 1 candle
            "change_5m": -0.45,   # % change last 5 candles (5m interval = 25 min)
            "trend": "UP" | "DOWN" | "FLAT",
            "side": "YES" | "NO" | "SKIP",
            "confidence": 0.0–1.0,
            "reason": "human-readable explanation"
          }
        """
        result = {
            "symbol": symbol,
            "price": 0.0,
            "change_1m": 0.0,
            "change_5m": 0.0,
            "trend": "FLAT",
            "side": "SKIP",
            "confidence": 0.0,
            "reason": "no data",
        }

        # Use 1m klines for sensitivity
        klines = await self.get_klines(symbol, interval="1m", limit=16)
        if len(klines) < 6:
            result["reason"] = "insufficient candle data"
            return result

        closes = [float(k[4]) for k in klines]
        current = closes[-1]
        one_min_ago = closes[-2]
        five_min_ago = closes[-6]
        fifteen_min_ago = closes[0]  # ~15 candles back

        change_1m = (current - one_min_ago) / one_min_ago * 100
        change_5m = (current - five_min_ago) / five_min_ago * 100
        change_15m = (current - fifteen_min_ago) / fifteen_min_ago * 100

        result["price"] = current
        result["change_1m"] = round(change_1m, 4)
        result["change_5m"] = round(change_5m, 4)

        # Volume check: recent candles vs average
        volumes = [float(k[5]) for k in klines]
        avg_vol = sum(volumes[:-3]) / max(len(volumes[:-3]), 1)
        recent_vol = sum(volumes[-3:]) / 3
        vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

        # Scoring: combine 1m + 5m signals
        # Weights: 1m = 0.4, 5m = 0.4, 15m = 0.2
        score = (change_1m * 0.4) + (change_5m * 0.4) + (change_15m * 0.2)

        # Confidence from magnitude and volume confirmation
        magnitude = abs(score)
        confidence = min(magnitude / 0.3, 1.0)  # full confidence at 0.8% move
        if vol_ratio > 1.5:
            confidence = min(confidence * 1.2, 1.0)  # boost if high volume

        # Decision thresholds
        THRESHOLD = 0.04  # minimum % move to act

        if score > THRESHOLD:
            trend = "UP"
            side = "YES"
        elif score < -THRESHOLD:
            trend = "DOWN"
            side = "NO"
        else:
            trend = "FLAT"
            side = "SKIP"
            confidence = 0.0

        result.update({
            "trend": trend,
            "side": side,
            "confidence": round(confidence, 3),
            "reason": (
                f"{symbol}: 1m={change_1m:+.3f}%  5m={change_5m:+.3f}%  "
                f"15m={change_15m:+.3f}%  vol_ratio={vol_ratio:.1f}x  "
                f"score={score:+.3f}  conf={confidence:.0%}"
            ),
        })
        return result

    async def get_signal_for_market(self, market_question: str) -> Optional[dict]:
        """Get signal using market name to determine the symbol."""
        symbol = _market_to_symbol(market_question)
        if not symbol:
            return None
        return await self.get_signal(symbol)


# Singleton
_feed: Optional[PriceFeed] = None

def get_price_feed() -> PriceFeed:
    global _feed
    if _feed is None:
        _feed = PriceFeed()
    return _feed
