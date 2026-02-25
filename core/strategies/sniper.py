"""
Orderbook Sniper Strategy
Places standing low-price orders across multiple markets.

Based on polymarket-terminal/src/sniper.js and services/sniperExecutor.js
Key patterns:
- GTC limit orders at very low price (e.g., $0.02)
- Both YES and NO sides simultaneously
- Cost: sniper_price * sniper_shares * 2 sides * asset_count
- Entry constraint: min 30s buffer (from sniperDetector.js)
- seenKeys cache: prevents duplicate orders per asset-slot
- Auto-sell on fill at target price
- Profit: 5-10x return when filled at low price
"""
import asyncio
import logging
import math
import time
import uuid
from typing import Optional

import config as cfg
from core.polymarket.client import get_client
from core.risk.manager import RiskError, get_risk_manager
from core.risk.portfolio import MarketPosition, TradeRecord, get_portfolio
from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

SLOT_DURATION = 300
MIN_BUFFER_SECS = 30  # Never enter if less than 30s into window


class Sniper(BaseStrategy):
    """
    Orderbook sniper: standing low-price orders waiting for panic sellers.

    Strategy:
    1. Find active 5-minute markets for sniper assets
    2. Place tiny GTC buy orders on BOTH YES and NO at SNIPER_PRICE
    3. If filled (panic seller or liquidation) -> profit!
    4. Auto-sell filled orders at SNIPER_SELL_TARGET
    5. Cost-efficient: $0.02 * 50 shares * 2 sides * 3 assets = $6 total
    """

    def __init__(self):
        super().__init__("Sniper")
        self._seen_keys: set[str] = set()  # "ASSET-SLOT" to prevent duplicates
        self._standing_orders: dict[str, dict] = {}  # order_id -> order info
        self._filled_orders: list[dict] = []

    async def on_start(self):
        total_cost = (
            cfg.SNIPER_PRICE
            * cfg.SNIPER_SHARES
            * 2  # both sides
            * len(cfg.SNIPER_ASSETS)
        )
        self.emit_alert(
            "success",
            f"Sniper started: {', '.join(cfg.SNIPER_ASSETS)} "
            f"@${cfg.SNIPER_PRICE} cost=${total_cost:.2f}/cycle"
        )

    async def on_stop(self):
        # Cancel all standing orders on stop
        if self._standing_orders and not cfg.DRY_RUN:
            self.emit_alert("info", f"Cancelling {len(self._standing_orders)} standing orders")
            for order_id in list(self._standing_orders.keys()):
                await self._client.cancel_order(order_id)
        self.emit_alert("info", "Sniper stopped")

    async def run_once(self):
        """Place new sniper orders for upcoming slots."""
        await self._place_sniper_orders()
        await self._check_fills()
        await asyncio.sleep(30)  # Re-check every 30s

    async def _place_sniper_orders(self):
        """
        Find markets and place standing low-price orders.
        From sniperDetector.js: check current + next slot,
        minimum 30s buffer for current slot.
        """
        now = int(time.time())
        current_slot = math.floor(now / SLOT_DURATION) * SLOT_DURATION
        time_into_slot = now - current_slot

        for asset in cfg.SNIPER_ASSETS:
            # Try current slot (if enough time buffer)
            if time_into_slot > MIN_BUFFER_SECS:
                slot_key = f"{asset}-{current_slot}"
                if slot_key not in self._seen_keys:
                    await self._snipe_asset_slot(asset, current_slot, slot_key)

            # Also try next slot
            next_slot = current_slot + SLOT_DURATION
            next_key = f"{asset}-{next_slot}"
            if next_key not in self._seen_keys:
                await self._snipe_asset_slot(asset, next_slot, next_key)

    async def _snipe_asset_slot(self, asset: str, slot: int, slot_key: str):
        """Place sniper orders for a specific asset slot."""
        # Find matching market
        markets = await self._client.search_markets(f"{asset} updown 5m", limit=10)

        target_market = None
        for m in markets:
            question = m.get("question", "").lower()
            if asset.lower() in question:
                target_market = m
                break

        if not target_market:
            return

        condition_id = target_market.get("condition_id") or target_market.get("conditionId", "")
        if not condition_id:
            return

        tokens = target_market.get("tokens") or target_market.get("outcomes", [])
        if not tokens or len(tokens) < 2:
            return

        # Get YES/NO tokens
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
            return

        self._seen_keys.add(slot_key)
        question = target_market.get("question", f"{asset} market")

        self.emit_alert(
            "info",
            f"Sniping: {question[:30]} @${cfg.SNIPER_PRICE} x{cfg.SNIPER_SHARES}"
        )

        # Place orders on BOTH sides
        for token_id, side_name in [(yes_token, "YES"), (no_token, "NO")]:
            resp = await self._client.place_limit_order(
                token_id,
                "BUY",
                cfg.SNIPER_PRICE,
                cfg.SNIPER_SHARES,
            )
            if resp:
                order_id = resp.get("order_id", f"sniper_{uuid.uuid4().hex[:8]}")
                self._standing_orders[order_id] = {
                    "order_id": order_id,
                    "token_id": token_id,
                    "condition_id": condition_id,
                    "market_name": question,
                    "asset": asset,
                    "side": side_name,
                    "price": cfg.SNIPER_PRICE,
                    "shares": cfg.SNIPER_SHARES,
                    "potential_payout": cfg.SNIPER_SHARES * 1.0,  # $1 per share if wins
                    "placed_at": int(time.time()),
                }
                logger.debug(f"Sniper order placed: {side_name} {order_id}")

    async def _check_fills(self):
        """
        Check if any standing orders were filled.
        If filled -> place auto-sell at target price.
        """
        if not self._standing_orders:
            return

        try:
            open_orders = await self._client.get_open_orders()
            open_ids = {o.get("id") or o.get("order_id") for o in open_orders}

            for order_id, order_info in list(self._standing_orders.items()):
                if order_id not in open_ids:
                    # Order no longer open = filled or cancelled
                    await self._handle_fill(order_id, order_info)
                    del self._standing_orders[order_id]
        except Exception as e:
            logger.warning(f"Sniper fill check failed: {e}")

    async def _handle_fill(self, order_id: str, order_info: dict):
        """Handle a filled sniper order - place sell at target."""
        self.emit_alert(
            "success",
            f"SNIPER FILL! {order_info['market_name'][:25]} "
            f"{order_info['side']} @${order_info['price']:.2f} "
            f"x{order_info['shares']}"
        )

        # Record position
        position = MarketPosition(
            condition_id=order_info["condition_id"],
            token_id=order_info["token_id"],
            market_name=order_info["market_name"],
            outcome=order_info["side"],
            shares=order_info["shares"],
            avg_buy_price=order_info["price"],
            total_cost=order_info["price"] * order_info["shares"],
            strategy="sniper",
        )
        self._portfolio.add_position(position)

        # Auto-sell at target
        sell_resp = await self._client.place_limit_order(
            order_info["token_id"],
            "SELL",
            cfg.SNIPER_SELL_TARGET,
            order_info["shares"],
        )

        if sell_resp:
            sell_id = sell_resp.get("order_id", "")
            self._portfolio.update_position(
                position.position_id,
                sell_order_id=sell_id,
                status="selling",
            )
            expected_profit = (
                (cfg.SNIPER_SELL_TARGET - order_info["price"]) * order_info["shares"]
            )
            self.emit_alert(
                "success",
                f"Sell set @${cfg.SNIPER_SELL_TARGET:.2f} "
                f"expected profit: +${expected_profit:.2f}"
            )

        # Record trade
        self._portfolio.record_trade(TradeRecord(
            trade_id=order_id,
            strategy="sniper",
            market_name=order_info["market_name"],
            condition_id=order_info["condition_id"],
            token_id=order_info["token_id"],
            side="BUY",
            price=order_info["price"],
            size=order_info["shares"],
            total=order_info["price"] * order_info["shares"],
            status="FILLED",
        ))

    def get_status(self) -> dict:
        return {
            **super().get_status(),
            "standing_orders": len(self._standing_orders),
            "assets": cfg.SNIPER_ASSETS,
            "price": cfg.SNIPER_PRICE,
            "target": cfg.SNIPER_SELL_TARGET,
            "total_cost": (
                cfg.SNIPER_PRICE * cfg.SNIPER_SHARES * 2 * len(cfg.SNIPER_ASSETS)
            ),
        }
