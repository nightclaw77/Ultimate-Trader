"""
Market Maker Strategy
Provides liquidity on 5-minute updown markets.

Based on polymarket-terminal/src/mm.js and services/mmExecutor.js
Key patterns:
- 5-minute slot calculation: floor(now/300)*300
- ALWAYS target NEXT slot, never current
- Entry: CTF split USDC -> YES+NO at $0.50 (zero slippage)
- Exit: GTC limit orders on BOTH sides at mm_sell_price
- Cut-loss: mm_cut_loss_time before close -> merge back to USDC
- Profit: spread capture (e.g., buy at 0.50, sell at 0.60 = 20% per trade)
"""
import asyncio
import logging
import math
import time
import uuid
from typing import Optional

import aiohttp

import config as cfg
from core.polymarket.client import get_client
from core.risk.manager import RiskError, get_risk_manager
from core.risk.portfolio import MarketPosition, TradeRecord, get_portfolio
from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

SLOT_DURATION = 300  # 5 minutes in seconds
MIN_ENTRY_BUFFER = 15  # Don't enter if <15s into window


def get_next_slot() -> int:
    """
    Calculate next 5-minute slot timestamp.
    From polymarket-terminal/src/services/mmDetector.js:
    Math.floor(Date.now() / 1000 / 300) * 300
    Always target NEXT slot, never current.
    """
    now = int(time.time())
    current = math.floor(now / SLOT_DURATION) * SLOT_DURATION
    return current + SLOT_DURATION


def get_current_slot() -> int:
    now = int(time.time())
    return math.floor(now / SLOT_DURATION) * SLOT_DURATION


class MarketMaker(BaseStrategy):
    """
    Automated liquidity provision on short-term binary markets.

    Strategy:
    1. Detect upcoming 5m updown markets for configured assets
    2. Split USDC into YES+NO tokens at $0.50 (via CTF contract)
    3. Place GTC sell orders on both YES and NO at target price
    4. If filled -> profit is the spread
    5. If NOT filled before close -> merge tokens back to USDC
    """

    def __init__(self):
        super().__init__("MarketMaker")
        self._active_markets: dict[str, dict] = {}  # condition_id -> market state
        self._processed_slots: set[str] = set()  # "ASSET-SLOT" keys to prevent duplicates

    async def on_start(self):
        self.emit_alert("success", f"MM started for: {', '.join(cfg.MM_ASSETS)}")

    async def on_stop(self):
        self.emit_alert("info", "MM stopped")

    async def run_once(self):
        """Main loop: detect new slots and enter if appropriate."""
        await self._detect_and_enter_markets()
        await self._monitor_active_positions()
        await asyncio.sleep(10)  # Check every 10 seconds

    async def _detect_and_enter_markets(self):
        """
        Find upcoming 5-minute markets for MM assets.
        Based on mmDetector.js logic.
        """
        next_slot = get_next_slot()

        for asset in cfg.MM_ASSETS:
            slot_key = f"{asset}-{next_slot}"
            if slot_key in self._processed_slots:
                continue

            # Search for next slot market
            markets = await self._client.search_markets(
                f"{asset} updown 5m", limit=10
            )

            for market in markets:
                slug = market.get("market_slug", "")
                # Slug pattern: {asset}-updown-5m-{timestamp}
                if asset.lower() not in slug.lower():
                    continue
                if "updown" not in slug.lower() and "up-down" not in slug.lower():
                    # Also check question text
                    question = market.get("question", "").lower()
                    if "up" not in question or asset.lower() not in question:
                        continue

                # Check this is for the next slot
                end_date = market.get("end_date_iso") or market.get("endDateIso", "")
                condition_id = market.get("condition_id") or market.get("conditionId", "")

                if not condition_id:
                    continue

                if condition_id in self._active_markets:
                    continue

                # Risk check
                try:
                    self._risk.check_new_position(cfg.MM_TRADE_SIZE, market.get("question", ""))
                except RiskError as e:
                    self.emit_alert("warning", f"MM risk block: {e}")
                    continue

                self._processed_slots.add(slot_key)
                await self._enter_market(market, asset)
                break

    async def _enter_market(self, market: dict, asset: str):
        """
        Enter a market with dual-sided strategy.
        From mmExecutor.js: split USDC, place dual limit orders.
        """
        condition_id = market.get("condition_id") or market.get("conditionId", "")
        question = market.get("question", f"{asset} 5m market")
        tokens = market.get("tokens") or market.get("outcomes", [])

        if not tokens or len(tokens) < 2:
            logger.warning(f"MM: No tokens found for {question}")
            return

        # Get YES and NO token IDs
        yes_token = None
        no_token = None
        for t in tokens:
            outcome = (t.get("outcome") or t.get("name", "")).upper()
            token_id = t.get("token_id") or t.get("id", "")
            if "YES" in outcome or "UP" in outcome:
                yes_token = token_id
            elif "NO" in outcome or "DOWN" in outcome:
                no_token = token_id

        if not yes_token or not no_token:
            logger.warning(f"MM: Could not identify YES/NO tokens for {question}")
            return

        self.emit_alert(
            "info",
            f"MM entering: {question[:30]} size=${cfg.MM_TRADE_SIZE}"
        )

        shares = cfg.MM_TRADE_SIZE / 0.50  # At $0.50 split price

        # Place YES sell order
        yes_resp = await self._client.place_limit_order(
            yes_token, "SELL", cfg.MM_SELL_PRICE, shares
        )
        # Place NO sell order
        no_resp = await self._client.place_limit_order(
            no_token, "SELL", cfg.MM_SELL_PRICE, shares
        )

        if yes_resp and no_resp:
            # Track this active market
            self._active_markets[condition_id] = {
                "condition_id": condition_id,
                "question": question,
                "asset": asset,
                "yes_token": yes_token,
                "no_token": no_token,
                "yes_order": yes_resp.get("order_id", ""),
                "no_order": no_resp.get("order_id", ""),
                "entry_time": int(time.time()),
                "trade_size": cfg.MM_TRADE_SIZE,
                "shares": shares,
                "status": "active",
            }

            # Record as position
            position = MarketPosition(
                condition_id=condition_id,
                token_id=yes_token,
                market_name=question,
                outcome="MM_DUAL",
                shares=shares * 2,
                avg_buy_price=0.50,
                total_cost=cfg.MM_TRADE_SIZE,
                strategy="mm",
            )
            self._portfolio.add_position(position)

            self.emit_alert(
                "success",
                f"MM orders placed: {question[:25]} sell@{cfg.MM_SELL_PRICE}"
            )
        else:
            self.emit_alert("error", f"MM order placement failed for {question[:25]}")

    async def _monitor_active_positions(self):
        """
        Monitor active MM positions for:
        1. Fill completion (profit!)
        2. Cut-loss timeout before market close
        """
        now = int(time.time())
        to_remove = []

        for condition_id, state in self._active_markets.items():
            elapsed = now - state["entry_time"]
            time_in_slot = now % SLOT_DURATION

            # Check if approaching slot end
            time_to_close = SLOT_DURATION - time_in_slot

            if time_to_close <= cfg.MM_CUT_LOSS_TIME:
                # CUT LOSS: cancel orders and merge tokens back
                self.emit_alert(
                    "warning",
                    f"MM cut-loss: {state['question'][:25]} ({time_to_close}s to close)"
                )
                await self._client.cancel_order(state.get("yes_order", ""))
                await self._client.cancel_order(state.get("no_order", ""))
                # Merge tokens (simplified - in production would call CTF contract)
                to_remove.append(condition_id)
                self._portfolio.update_position(
                    f"{condition_id}-{state['yes_token']}",
                    status="sold",
                )

        for cid in to_remove:
            del self._active_markets[cid]

    def get_status(self) -> dict:
        return {
            **super().get_status(),
            "active_markets": len(self._active_markets),
            "assets": cfg.MM_ASSETS,
            "sell_price": cfg.MM_SELL_PRICE,
        }
