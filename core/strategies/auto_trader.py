"""
AutoTrader Strategy — Autonomous Paper Trading on Crypto Up/Down Markets

Logic:
  1. Every 30s: find upcoming "Up or Down" BTC/ETH/SOL/XRP markets
  2. Get real Binance price + momentum signal
  3. Decide YES or NO based on: momentum direction + strength
  4. Paper-buy if confidence > threshold and market is still ~50/50
  5. Auto-sell at +15% profit OR -10% stop-loss OR market close

Treats virtual $50 as real — every trade is sized carefully.
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import config as cfg
from core.analysis.price_feed import get_price_feed
from core.notifications import get_notifier
from core.paper_trading.smart_executor import SmartExecutor
from core.polymarket.client import get_client
from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

# Crypto keywords to detect in market questions
CRYPTO_KEYWORDS = ["bitcoin", "ethereum", "solana", "xrp", "eth ", "btc", "sol "]

# Only trade markets ending in this time window (minutes)
MIN_MINUTES_TO_CLOSE = 2      # don't enter if < 2 min left
MAX_MINUTES_TO_CLOSE = 90     # don't enter > 90 min markets

# Trade decision thresholds
MIN_CONFIDENCE = cfg.AUTO_MIN_CONFIDENCE         # minimum momentum confidence to place trade
MAX_MARKET_PROB = 0.62        # skip if market already priced > 62% one side
MIN_MARKET_PROB = 0.38        # skip if market already priced < 38% YES

# Position sizing (of virtual balance)
BASE_TRADE_SIZE = cfg.BASE_TRADE_SIZE         # $3 base per trade
MAX_TRADE_SIZE = cfg.MAX_TRADE_SIZE          # $8 max per trade
MAX_OPEN_AUTO_TRADES = cfg.MAX_OPEN_AUTO_TRADES      # max concurrent auto positions

# Exit targets
PROFIT_TARGET = cfg.AUTO_PROFIT_TARGET          # sell when profit > 14% (price moves from 0.50 to 0.57)
STOP_LOSS = -cfg.AUTO_STOP_LOSS             # sell when loss > 10%


@dataclass
class AutoPosition:
    market_question: str
    condition_id: str
    token_id: str        # YES or NO token
    side: str            # "YES" or "NO"
    symbol: str          # "BTCUSDT"
    entry_price: float   # price paid
    size_usdc: float     # dollars spent
    shares: float        # shares bought
    entry_signal: dict   # snapshot of signal at entry
    entered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    market_end: Optional[datetime] = None
    closed: bool = False
    close_reason: str = ""
    pnl: float = 0.0
    position_id: str = ""


class AutoTrader(BaseStrategy):
    """
    Autonomous trader: scans live crypto Up/Down markets, reads momentum
    from Binance, and paper-trades when there's a clear directional signal.
    """

    def __init__(self):
        super().__init__("AutoTrader")
        self._client = get_client()
        self._feed = get_price_feed()
        self._executor = SmartExecutor()
        self._notifier = get_notifier()
        self._positions: list[AutoPosition] = []
        self._scanned_ids: set = set()   # market ids already traded this session
        self._scan_interval = 30         # seconds between market scans
        self._monitor_interval = 15      # seconds between position checks

    # ── Main loop ─────────────────────────────────────────────

    async def on_start(self):
        await self._executor.start()
        self.emit_alert("info", "AutoTrader ready — scanning crypto Up/Down markets")

    async def on_stop(self):
        await self._feed.close()

    async def run_once(self):
        """Called by base strategy loop — scan markets then check positions."""
        await self._scan_and_trade()
        await self._monitor_positions()
        await asyncio.sleep(self._scan_interval)

    async def _scan_and_trade(self):
        """Find upcoming crypto Up/Down markets and decide whether to trade."""
        open_count = sum(1 for p in self._positions if not p.closed)
        if open_count >= MAX_OPEN_AUTO_TRADES:
            return

        markets = await self._find_updown_markets()
        if not markets:
            return

        for market in markets:
            if open_count >= MAX_OPEN_AUTO_TRADES:
                break

            mid = market.get("conditionId") or market.get("id", "")
            if mid in self._scanned_ids:
                continue

            minutes_left = self._minutes_to_close(market)
            if minutes_left is None:
                continue
            if not (MIN_MINUTES_TO_CLOSE <= minutes_left <= MAX_MINUTES_TO_CLOSE):
                continue

            # Get momentum signal from Binance
            signal = await self._feed.get_signal_for_market(market.get("question", ""))
            if signal is None or signal["side"] == "SKIP":
                continue
            if signal["confidence"] < MIN_CONFIDENCE:
                continue

            # Check market hasn't already moved too far
            prices_raw = market.get("outcomePrices") or "[]"
            try:
                prices = json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw
                yes_prob = float(prices[0]) if prices else 0.5
            except Exception:
                yes_prob = 0.5

            if yes_prob > MAX_MARKET_PROB or yes_prob < MIN_MARKET_PROB:
                self.emit_alert("info",
                    f"[AutoTrader] {market.get('question','')[:40]}: "
                    f"prob {yes_prob:.0%} too one-sided, skip"
                )
                self._scanned_ids.add(mid)
                continue

            # Place the trade
            placed = await self._enter_trade(market, signal, yes_prob)
            if placed:
                open_count += 1
                self._scanned_ids.add(mid)

    async def _enter_trade(self, market: dict, signal: dict, yes_prob: float) -> bool:
        """Execute a paper trade based on signal."""
        question = market.get("question", "")
        token_ids_raw = market.get("clobTokenIds") or "[]"
        try:
            token_ids = json.loads(token_ids_raw) if isinstance(token_ids_raw, str) else token_ids_raw
        except Exception:
            logger.warning(f"[AutoTrader] bad clobTokenIds for {question[:40]}")
            return False

        if not token_ids:
            return False

        side = signal["side"]  # "YES" or "NO"
        # YES token is index 0, NO token is index 1
        token_idx = 0 if side == "YES" else 1
        token_id = token_ids[token_idx] if len(token_ids) > token_idx else token_ids[0]

        # Size: scale by confidence, clamp to range
        size = BASE_TRADE_SIZE + (MAX_TRADE_SIZE - BASE_TRADE_SIZE) * signal["confidence"]
        size = round(min(size, MAX_TRADE_SIZE), 2)

        # Get real current price for this token
        try:
            entry_price = await self._client.get_price(token_id, side="BUY")
            if entry_price <= 0:
                entry_price = yes_prob if side == "YES" else (1 - yes_prob)
        except Exception:
            entry_price = yes_prob if side == "YES" else (1 - yes_prob)

        if entry_price <= 0 or entry_price >= 1:
            return False

        shares = size / entry_price

        # Paper execute
        result = await self._executor.buy(
            token_id=token_id,
            condition_id=market.get("conditionId") or market.get("id", ""),
            market_name=question[:50],
            outcome=side,
            usdc_amount=size,
            strategy="auto",
        )

        if not result:
            return False

        # Parse market end time
        end_dt = None
        end_str = market.get("endDateIso") or market.get("endDate", "")
        if end_str:
            try:
                end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            except Exception:
                pass

        condition_id = market.get("conditionId") or market.get("id", "")
        pos = AutoPosition(
            market_question=question,
            condition_id=condition_id,
            token_id=token_id,
            side=side,
            symbol=signal["symbol"],
            entry_price=entry_price,
            size_usdc=size,
            shares=shares,
            entry_signal=signal,
            market_end=end_dt,
        )
        # Store paper position_id for sell calls
        if result and isinstance(result, dict):
            pos.position_id = result.get("trade_id", pos.token_id)

        self._positions.append(pos)

        minutes_left = self._minutes_to_close(market)
        self.emit_alert("success",
            f"[AUTO] {side} {question[:35]}… "
            f"${size:.2f} @ {entry_price:.3f}  "
            f"conf={signal['confidence']:.0%}  {minutes_left:.0f}min left"
        )
        self._notifier.trade_opened(
            strategy="AutoTrader",
            market=question[:50],
            side=side,
            price=entry_price,
            size=size,
        )
        logger.info(
            f"[AutoTrader] Entered {side} {question[:40]} "
            f"${size:.2f}@{entry_price:.3f} | {signal['reason']}"
        )
        return True

    async def _monitor_positions(self):
        """Check open positions for exit conditions."""
        for pos in self._positions:
            if pos.closed:
                continue

            try:
                current_price = await self._client.get_price(pos.token_id, side="BUY")
                if current_price <= 0:
                    continue
            except Exception:
                continue

            pnl_pct = (current_price - pos.entry_price) / pos.entry_price
            pos.pnl = (current_price - pos.entry_price) * pos.shares

            now = datetime.now(timezone.utc)
            minutes_left = None
            if pos.market_end:
                minutes_left = (pos.market_end - now).total_seconds() / 60

            # Exit conditions
            reason = None
            if pnl_pct >= PROFIT_TARGET:
                reason = f"PROFIT +{pnl_pct:.1%}"
            elif pnl_pct <= STOP_LOSS:
                reason = f"STOP {pnl_pct:.1%}"
            elif minutes_left is not None and minutes_left <= 1.5:
                reason = f"TIME ({minutes_left:.1f}min left)"

            if reason:
                await self._exit_trade(pos, current_price, reason)

    async def _exit_trade(self, pos: AutoPosition, exit_price: float, reason: str):
        """Close a position."""
        pos.closed = True
        pos.close_reason = reason

        result = await self._executor.sell(
            position_id=getattr(pos, "position_id", pos.token_id),
            token_id=pos.token_id,
            strategy="auto",
        )

        pnl = (exit_price - pos.entry_price) * pos.shares
        color = "green" if pnl >= 0 else "red"
        self.emit_alert(
            "success" if pnl >= 0 else "warning",
            f"[AUTO CLOSE] {pos.side} {pos.market_question[:30]}… "
            f"→ {reason}  PnL: ${pnl:+.2f}"
        )
        self._notifier.trade_closed(
            strategy="AutoTrader",
            market=pos.market_question[:50],
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
        )
        logger.info(
            f"[AutoTrader] Closed {pos.side} {pos.market_question[:40]} "
            f"@ {exit_price:.3f}  reason={reason}  PnL=${pnl:+.2f}"
        )

    # ── Helpers ───────────────────────────────────────────────

    async def _find_updown_markets(self) -> list[dict]:
        """Scan live markets and return crypto Up/Down ones sorted by time-to-close."""
        try:
            all_markets = await self._client.get_markets(limit=100, order="volume")
        except Exception:
            return []

        now = datetime.now(timezone.utc)
        results = []

        for m in all_markets:
            question = (m.get("question") or "").lower()

            # Must be an Up/Down or price prediction market for known crypto
            is_crypto = any(k in question for k in CRYPTO_KEYWORDS)
            is_updown = "up or down" in question or "will the price of" in question
            if not (is_crypto and is_updown):
                continue

            if not m.get("acceptingOrders"):
                continue

            minutes = self._minutes_to_close(m)
            if minutes is None:
                continue
            if not (MIN_MINUTES_TO_CLOSE <= minutes <= MAX_MINUTES_TO_CLOSE):
                continue

            results.append(m)

        # Sort by time-to-close ascending (soonest first — more urgency = 5-min priority)
        results.sort(key=lambda m: self._minutes_to_close(m) or 9999)
        return results

    def _minutes_to_close(self, market: dict) -> Optional[float]:
        end_str = market.get("endDateIso") or market.get("endDate", "")
        if not end_str:
            return None
        try:
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            delta = (end_dt - datetime.now(timezone.utc)).total_seconds() / 60
            return delta
        except Exception:
            return None

    def get_open_positions(self) -> list[AutoPosition]:
        return [p for p in self._positions if not p.closed]

    def get_all_positions(self) -> list[AutoPosition]:
        return list(self._positions)

    def get_stats(self) -> dict:
        closed = [p for p in self._positions if p.closed]
        wins = [p for p in closed if p.pnl > 0]
        total_pnl = sum(p.pnl for p in closed)
        return {
            "total_trades": len(closed),
            "open_trades": len(self._positions) - len(closed),
            "wins": len(wins),
            "win_rate": len(wins) / max(len(closed), 1) * 100,
            "total_pnl": total_pnl,
        }
