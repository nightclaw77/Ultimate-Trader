"""
AutoTrader Strategy — Autonomous Paper Trading on Crypto Markets

Two market types:
  A) "Up or Down" 5-15 min markets: momentum-based prediction
  B) "Will the price of X be above Y" markets: price-vs-target analysis

Scans every 30s, enters paper trades when signal is clear.
Treats virtual $50 as real money — careful sizing.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import config as cfg
from core.analysis.price_feed import get_price_feed, SYMBOL_MAP
from core.notifications import get_notifier
from core.paper_trading.smart_executor import SmartExecutor
from core.polymarket.client import get_client
from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

CRYPTO_KEYWORDS = ["bitcoin", "ethereum", "solana", "xrp", "eth ", "btc ", "sol "]

# For parsing "Will the price of X be above $68,000"
PRICE_RE = re.compile(
    r"will the price of (\w+) be (?:above|below|between) \$?([\d,]+)",
    re.IGNORECASE,
)


@dataclass
class AutoPosition:
    market_question: str
    condition_id: str
    token_id: str
    side: str            # "YES" or "NO"
    symbol: str          # "BTCUSDT"
    entry_price: float
    size_usdc: float
    shares: float
    entry_signal: dict
    entered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    market_end: Optional[datetime] = None
    closed: bool = False
    close_reason: str = ""
    pnl: float = 0.0
    position_id: str = ""


class AutoTrader(BaseStrategy):

    def __init__(self):
        super().__init__("AutoTrader")
        self._client = get_client()
        self._feed = get_price_feed()
        self._executor = SmartExecutor()
        self._notifier = get_notifier()
        self._positions: list[AutoPosition] = []
        self._traded_ids: set = set()
        self._scan_count = 0

    async def on_start(self):
        await self._executor.start()
        logger.info("[AutoTrader] Started — scanning crypto markets")
        self.emit_alert("info", "AutoTrader ON — scanning crypto Up/Down + price markets")

    async def on_stop(self):
        await self._feed.close()

    async def run_once(self):
        self._scan_count += 1
        await self._scan_and_trade()
        await self._monitor_positions()
        await asyncio.sleep(30)

    # ── SCAN ──────────────────────────────────────────────────

    async def _scan_and_trade(self):
        open_count = sum(1 for p in self._positions if not p.closed)
        if open_count >= cfg.MAX_OPEN_AUTO_TRADES:
            return

        markets = await self._find_crypto_markets()
        if not markets:
            if self._scan_count % 4 == 1:  # log every ~2min
                logger.info("[AutoTrader] Scan #%d: no eligible crypto markets found", self._scan_count)
            return

        logger.info("[AutoTrader] Scan #%d: found %d eligible markets", self._scan_count, len(markets))

        for market in markets:
            if open_count >= cfg.MAX_OPEN_AUTO_TRADES:
                break

            mid = market.get("conditionId") or market.get("id", "")
            if mid in self._traded_ids:
                continue

            decision = await self._analyze_market(market)
            if decision is None:
                continue

            side, confidence, reason = decision
            if confidence < cfg.AUTO_MIN_CONFIDENCE:
                logger.info("[AutoTrader] SKIP %s: conf=%.0f%% < %.0f%% | %s",
                    market.get("question","")[:35], confidence*100, cfg.AUTO_MIN_CONFIDENCE*100, reason)
                continue

            placed = await self._enter_trade(market, side, confidence, reason)
            if placed:
                open_count += 1
                self._traded_ids.add(mid)

    async def _analyze_market(self, market: dict) -> Optional[tuple]:
        """
        Analyze a market. Returns (side, confidence, reason) or None to skip.
        """
        question = market.get("question", "")
        q_lower = question.lower()

        # Check current market probability
        yes_prob = self._get_yes_prob(market)
        if yes_prob is not None and (yes_prob > 0.62 or yes_prob < 0.38):
            return None  # too one-sided, already priced

        # Type A: "Up or Down" market → momentum signal
        if "up or down" in q_lower:
            signal = await self._feed.get_signal_for_market(question)
            if signal is None or signal["side"] == "SKIP":
                return None
            return (signal["side"], signal["confidence"], signal["reason"])

        # Type B: "Will the price of X be above $Y" → price vs target
        match = PRICE_RE.search(question)
        if match:
            asset_name = match.group(1).lower()
            target_str = match.group(2).replace(",", "")
            try:
                target_price = float(target_str)
            except ValueError:
                return None

            symbol = None
            for key, sym in SYMBOL_MAP.items():
                if key in asset_name:
                    symbol = sym
                    break
            if not symbol:
                return None

            current = await self._feed.get_price(symbol)
            if current <= 0:
                return None

            # How far is current price from target?
            diff_pct = (current - target_price) / target_price * 100

            # If BTC=$68,500 and target=$68,000 → diff=+0.7% → YES is likely
            # If BTC=$67,000 and target=$68,000 → diff=-1.5% → NO is likely
            if diff_pct > 1.0:
                # Price well above target → YES
                conf = min(abs(diff_pct) / 5.0, 0.95)
                # But if market already says YES > 60%, it's priced in
                if yes_prob and yes_prob > 0.58:
                    conf *= 0.5
                reason = f"{symbol} ${current:,.0f} vs target ${target_price:,.0f} ({diff_pct:+.1f}%) → YES"
                return ("YES", conf, reason)
            elif diff_pct < -1.0:
                # Price well below target → NO
                conf = min(abs(diff_pct) / 5.0, 0.95)
                if yes_prob and yes_prob < 0.42:
                    conf *= 0.5
                reason = f"{symbol} ${current:,.0f} vs target ${target_price:,.0f} ({diff_pct:+.1f}%) → NO"
                return ("NO", conf, reason)
            else:
                # Too close to call
                return None

        return None

    # ── ENTER/EXIT ────────────────────────────────────────────

    async def _enter_trade(self, market: dict, side: str, confidence: float, reason: str) -> bool:
        question = market.get("question", "")
        token_ids = self._parse_token_ids(market)
        if not token_ids:
            return False

        token_idx = 0 if side == "YES" else 1
        if token_idx >= len(token_ids):
            token_idx = 0
        token_id = token_ids[token_idx]

        # Size: $3 base, scale up to $8 by confidence
        size = cfg.BASE_TRADE_SIZE + (cfg.MAX_TRADE_SIZE - cfg.BASE_TRADE_SIZE) * confidence
        size = round(min(size, cfg.MAX_TRADE_SIZE), 2)

        # Get real price
        entry_price = 0
        try:
            entry_price = await self._client.get_price(token_id, side="BUY")
        except Exception:
            pass
        if entry_price <= 0:
            yes_prob = self._get_yes_prob(market) or 0.5
            entry_price = yes_prob if side == "YES" else (1 - yes_prob)
        if entry_price <= 0.01 or entry_price >= 0.99:
            return False

        shares = size / entry_price
        condition_id = market.get("conditionId") or market.get("id", "")

        result = await self._executor.buy(
            token_id=token_id,
            condition_id=condition_id,
            market_name=question[:50],
            outcome=side,
            usdc_amount=size,
            strategy="auto",
        )
        if not result:
            logger.warning("[AutoTrader] Buy failed for %s", question[:40])
            return False

        end_dt = self._parse_end_date(market)
        symbol = ""
        for key, sym in SYMBOL_MAP.items():
            if key in question.lower():
                symbol = sym
                break

        pos = AutoPosition(
            market_question=question,
            condition_id=condition_id,
            token_id=token_id,
            side=side,
            symbol=symbol,
            entry_price=entry_price,
            size_usdc=size,
            shares=shares,
            entry_signal={"reason": reason, "confidence": confidence},
            market_end=end_dt,
        )
        if result and isinstance(result, dict):
            pos.position_id = result.get("trade_id", token_id)

        self._positions.append(pos)

        self.emit_alert("success",
            f"[AUTO BUY] {side} {question[:35]}… "
            f"${size:.2f} @ {entry_price:.3f} | conf={confidence:.0%}"
        )
        self._notifier.trade_opened(
            strategy="AutoTrader",
            market=question[:50],
            side=side,
            price=entry_price,
            size=size,
        )
        logger.info("[AutoTrader] ENTERED %s %s | $%.2f @ %.3f | %s",
            side, question[:40], size, entry_price, reason)
        return True

    async def _monitor_positions(self):
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

            reason = None
            if pnl_pct >= cfg.AUTO_PROFIT_TARGET:
                reason = f"PROFIT +{pnl_pct:.1%}"
            elif pnl_pct <= -cfg.AUTO_STOP_LOSS:
                reason = f"STOP {pnl_pct:.1%}"
            elif minutes_left is not None and minutes_left <= 1.5:
                reason = f"TIME ({minutes_left:.1f}min)"

            if reason:
                await self._exit_trade(pos, current_price, reason)

    async def _exit_trade(self, pos: AutoPosition, exit_price: float, reason: str):
        pos.closed = True
        pos.close_reason = reason

        await self._executor.sell(
            position_id=pos.position_id or pos.token_id,
            token_id=pos.token_id,
            strategy="auto",
        )

        pnl = (exit_price - pos.entry_price) * pos.shares
        self.emit_alert(
            "success" if pnl >= 0 else "warning",
            f"[AUTO SELL] {pos.side} {pos.market_question[:30]}… "
            f"{reason} PnL: ${pnl:+.2f}"
        )
        self._notifier.trade_closed(
            strategy="AutoTrader",
            market=pos.market_question[:50],
            side=pos.side,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
        )
        logger.info("[AutoTrader] CLOSED %s %s | reason=%s PnL=$%.2f",
            pos.side, pos.market_question[:40], reason, pnl)

    # ── HELPERS ───────────────────────────────────────────────

    async def _find_crypto_markets(self) -> list[dict]:
        try:
            all_markets = await self._client.get_markets(limit=100, order="volume")
        except Exception:
            return []

        now = datetime.now(timezone.utc)
        results = []

        for m in all_markets:
            question = (m.get("question") or "").lower()

            is_crypto = any(k in question for k in CRYPTO_KEYWORDS)
            is_tradeable = "up or down" in question or "will the price of" in question
            if not (is_crypto and is_tradeable):
                continue

            if not m.get("acceptingOrders"):
                continue

            # Use endDate (full datetime) not endDateIso (just date)
            end_dt = self._parse_end_date(m)
            if end_dt is None:
                continue

            minutes_left = (end_dt - now).total_seconds() / 60
            if minutes_left < 2:
                continue  # too close to resolution
            if minutes_left > 60 * 48:
                continue  # >48 hours, skip

            results.append(m)

        results.sort(key=lambda m: self._parse_end_date(m) or now)
        return results

    def _parse_end_date(self, market: dict) -> Optional[datetime]:
        """Parse endDate (full ISO datetime like 2026-02-27T02:45:00Z)."""
        end_str = market.get("endDate") or market.get("endDateIso", "")
        if not end_str:
            return None
        try:
            dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    def _get_yes_prob(self, market: dict) -> Optional[float]:
        raw = market.get("outcomePrices") or "[]"
        try:
            prices = json.loads(raw) if isinstance(raw, str) else raw
            return float(prices[0]) if prices else None
        except Exception:
            return None

    def _parse_token_ids(self, market: dict) -> list:
        raw = market.get("clobTokenIds") or "[]"
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return []

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
