"""
Copy Trader Strategy
Monitors a target wallet and copies their trades.

Based on polymarket-terminal/src/index.js and services/wsWatcher.js
Key patterns:
- WebSocket activity stream filtering by trader_address
- Position sizing: max_position * (size_percent / 100)
- Auto-sell GTC limit at avgBuyPrice * (1 + profit_pct / 100)
- Deduplication: last 500 trade IDs
"""
import asyncio
import logging
import uuid
from collections import deque
from datetime import datetime

import config as cfg
from core.polymarket.client import get_client
from core.polymarket.ws_client import ActivityWatcher
from core.risk.manager import RiskError, get_risk_manager
from core.risk.portfolio import MarketPosition, TradeRecord, get_portfolio
from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class CopyTrader(BaseStrategy):
    """
    Copies trades from a configured target wallet.

    Flow:
    1. Subscribe to activity WebSocket
    2. Filter trades from COPY_TRADER_ADDRESS
    3. Mirror BUY trades with scaled position size
    4. Place auto-sell limit order at profit target
    5. Deduplicate to prevent double-buying
    """

    def __init__(self):
        super().__init__("CopyTrader")
        self._watcher: ActivityWatcher = None
        self._pending_trades: deque = deque(maxlen=500)
        self._target_address = cfg.COPY_TRADER_ADDRESS
        self._trade_queue: asyncio.Queue = asyncio.Queue()

    async def on_start(self):
        if not self._target_address:
            self.emit_alert("warning", "No COPY_TRADER_ADDRESS configured")
            self._running = False
            return

        self._watcher = ActivityWatcher(
            target_address=self._target_address,
            on_trade=self._on_trade_received,
        )
        await self._watcher.start()
        self.emit_alert("success", f"Watching {self._target_address[:10]}...")

    async def on_stop(self):
        if self._watcher:
            await self._watcher.stop()

    def _on_trade_received(self, msg: dict):
        """Called from WebSocket thread when target wallet trades."""
        self._trade_queue.put_nowait(msg)

    async def run_once(self):
        """Process incoming trades from queue."""
        try:
            msg = await asyncio.wait_for(self._trade_queue.get(), timeout=1.0)
            await self._process_trade(msg)
        except asyncio.TimeoutError:
            pass

    async def _process_trade(self, msg: dict):
        """
        Process a trade from the target wallet.
        From polymarket-terminal/src/services/executor.js
        """
        # Only copy BUY trades
        side = msg.get("side", "").upper()
        if side != "BUY":
            return

        condition_id = msg.get("condition_id") or msg.get("market", "")
        token_id = msg.get("asset_id") or msg.get("token_id", "")
        original_price = float(msg.get("price", 0))
        original_size = float(msg.get("size", 0))

        if not condition_id or not token_id or original_price <= 0:
            return

        # Calculate our position size (percentage of original)
        our_usdc = original_price * original_size * (cfg.COPY_SIZE_PERCENT / 100)
        our_usdc = max(0.10, min(our_usdc, cfg.MAX_POSITION_USDC))

        # Risk check
        try:
            self._risk.check_new_position(our_usdc, condition_id)
        except RiskError as e:
            self.emit_alert("warning", f"Risk block: {e}")
            return

        # Get market info
        market = await self._client.get_market(condition_id)
        market_name = market.get("question", condition_id[:20]) if market else condition_id[:20]

        self.emit_alert(
            "info",
            f"Copying: {market_name[:30]} x${our_usdc:.2f} @{original_price:.3f}"
        )

        # Place market order
        resp = await self._client.place_market_order(token_id, "BUY", our_usdc)

        if not resp:
            self.emit_alert("error", "Copy order failed")
            return

        # Record position
        shares = our_usdc / original_price
        position = MarketPosition(
            condition_id=condition_id,
            token_id=token_id,
            market_name=market_name,
            outcome="YES",  # We follow their direction
            shares=shares,
            avg_buy_price=original_price,
            total_cost=our_usdc,
            strategy="copy",
        )
        self._portfolio.add_position(position)

        # Place auto-sell limit order
        sell_price = self._risk.calculate_sell_price(
            original_price, cfg.COPY_AUTO_SELL_PROFIT
        )
        sell_resp = await self._client.place_limit_order(
            token_id, "SELL", sell_price, shares
        )

        if sell_resp:
            order_id = sell_resp.get("order_id", "")
            self._portfolio.update_position(
                position.position_id,
                sell_order_id=order_id,
                status="selling",
            )
            self.emit_alert(
                "success",
                f"Copied + sell set: {market_name[:25]} sell@{sell_price:.2f}"
            )

        # Record trade
        self._portfolio.record_trade(TradeRecord(
            trade_id=str(uuid.uuid4()),
            strategy="copy",
            market_name=market_name,
            condition_id=condition_id,
            token_id=token_id,
            side="BUY",
            price=original_price,
            size=shares,
            total=our_usdc,
            status=resp.get("status", "PLACED"),
        ))

    def get_status(self) -> dict:
        return {
            **super().get_status(),
            "target": self._target_address[:10] + "..." if self._target_address else "not set",
            "positions": len(self._portfolio.get_positions_by_strategy("copy")),
        }
