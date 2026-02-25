"""
WebSocket Client for Polymarket Real-Time Data
Based on patterns from polymarket-terminal/src/services/wsWatcher.js
and nautilus_trader/adapters/polymarket/websocket/client.py
"""
import asyncio
import json
import logging
from collections import deque
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed

import config as cfg

logger = logging.getLogger(__name__)

# Heartbeat interval (from polymarket-terminal: 5s ping)
PING_INTERVAL = 5
# Reconnect delays: exponential backoff 2s -> 30s max
RECONNECT_DELAYS = [2, 4, 8, 16, 30]


class PolymarketWSClient:
    """
    WebSocket client for real-time Polymarket data.

    Handles:
    - Activity stream (trades from watched wallets)
    - Market data (order book updates)
    - Automatic reconnection with exponential backoff
    - Message deduplication (last 500 trade IDs, from polymarket-terminal)
    """

    def __init__(self, on_message: Callable[[dict], None]):
        self._on_message = on_message
        self._ws = None
        self._running = False
        self._reconnect_attempt = 0
        self._processed_ids: deque = deque(maxlen=500)  # dedup last 500
        self._subscriptions: list[dict] = []
        self._task: Optional[asyncio.Task] = None

    def subscribe_activity(self):
        """Subscribe to all trades activity stream."""
        self._subscriptions.append({
            "action": "subscribe",
            "subscriptions": [{"topic": "activity", "type": "trades"}],
        })

    def subscribe_market(self, asset_ids: list[str]):
        """Subscribe to order book updates for specific markets."""
        self._subscriptions.append({
            "operation": "subscribe",
            "channel": "market",
            "assets_ids": asset_ids,
        })

    async def connect(self):
        """Start WebSocket connection in background."""
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def disconnect(self):
        """Stop WebSocket connection."""
        self._running = False
        if self._task:
            self._task.cancel()
        if self._ws:
            await self._ws.close()

    async def _run(self):
        """Main connection loop with reconnection."""
        while self._running:
            try:
                await self._connect_and_listen()
                self._reconnect_attempt = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                if not self._running:
                    break
                delay = RECONNECT_DELAYS[
                    min(self._reconnect_attempt, len(RECONNECT_DELAYS) - 1)
                ]
                logger.warning(f"WS disconnected ({e}), reconnecting in {delay}s...")
                self._reconnect_attempt += 1
                await asyncio.sleep(delay)

    async def _connect_and_listen(self):
        """Connect and process messages until disconnection."""
        async with websockets.connect(
            cfg.WS_HOST,
            ping_interval=PING_INTERVAL,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            logger.info("WebSocket connected to Polymarket")

            # Send all subscriptions
            for sub in self._subscriptions:
                await ws.send(json.dumps(sub))
                logger.debug(f"Subscribed: {sub}")

            # Listen for messages
            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw_msg)
                    await self._handle_message(msg)
                except json.JSONDecodeError:
                    pass

    async def _handle_message(self, msg: dict):
        """
        Process incoming WebSocket message.
        Deduplicate using trade ID (from polymarket-terminal pattern).
        """
        if not isinstance(msg, dict):
            return

        # Extract trade ID for deduplication
        trade_id = msg.get("id") or msg.get("trade_id") or msg.get("taker_order_id")
        if trade_id:
            if trade_id in self._processed_ids:
                return
            self._processed_ids.append(trade_id)

        # Route to callback
        try:
            self._on_message(msg)
        except Exception as e:
            logger.error(f"Error in WS message handler: {e}")

    @property
    def is_connected(self) -> bool:
        return self._ws is not None and not self._ws.closed


class ActivityWatcher:
    """
    Watches Polymarket activity stream for trades from specific wallets.
    Used by Copy Trader strategy.
    Based on polymarket-terminal/src/services/wsWatcher.js
    """

    def __init__(self, target_address: str, on_trade: Callable[[dict], None]):
        self._target = target_address.lower()
        self._on_trade = on_trade
        self._ws = PolymarketWSClient(self._filter_trades)
        self._ws.subscribe_activity()

    def _filter_trades(self, msg: dict):
        """Filter trades from target wallet."""
        # Message format from polymarket-terminal analysis
        trader = (msg.get("trader_address") or "").lower()
        if trader != self._target:
            return

        trade_type = msg.get("type", "")
        if trade_type not in ("TRADE", ""):
            return

        self._on_trade(msg)

    async def start(self):
        await self._ws.connect()
        logger.info(f"Watching wallet: {self._target[:10]}...")

    async def stop(self):
        await self._ws.disconnect()
