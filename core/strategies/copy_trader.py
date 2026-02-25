"""
Copy Trader Strategy - with Paper Trading + Telegram support
"""
import asyncio
import logging
import uuid
from collections import deque

import config as cfg
from core.notifications import get_notifier
from core.paper_trading.smart_executor import SmartExecutor
from core.polymarket.ws_client import ActivityWatcher
from core.risk.manager import RiskError, get_risk_manager
from core.risk.portfolio import TradeRecord, get_portfolio
from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class CopyTrader(BaseStrategy):
    def __init__(self):
        super().__init__("CopyTrader")
        self._executor = SmartExecutor()
        self._notifier = get_notifier()
        self._watcher = None
        self._trade_queue = asyncio.Queue()

    async def on_start(self):
        await self._executor.start()
        if not cfg.COPY_TRADER_ADDRESS:
            self.emit_alert("warning", "No COPY_TRADER_ADDRESS set")
            self._running = False
            return
        self._watcher = ActivityWatcher(
            target_address=cfg.COPY_TRADER_ADDRESS,
            on_trade=lambda msg: self._trade_queue.put_nowait(msg),
        )
        await self._watcher.start()
        self.emit_alert("success", f"[{self._executor.mode}] Watching {cfg.COPY_TRADER_ADDRESS[:12]}...")

    async def on_stop(self):
        await self._executor.stop()
        if self._watcher:
            await self._watcher.stop()

    async def run_once(self):
        try:
            msg = await asyncio.wait_for(self._trade_queue.get(), timeout=1.0)
            await self._process_trade(msg)
        except asyncio.TimeoutError:
            pass

    async def _process_trade(self, msg: dict):
        side = msg.get("side", "").upper()
        if side != "BUY":
            return
        condition_id = msg.get("condition_id") or msg.get("market", "")
        token_id = msg.get("asset_id") or msg.get("token_id", "")
        original_price = float(msg.get("price", 0))
        original_size = float(msg.get("size", 0))
        if not condition_id or not token_id or original_price <= 0:
            return
        our_usdc = max(0.10, min(
            original_price * original_size * (cfg.COPY_SIZE_PERCENT / 100),
            cfg.MAX_POSITION_USDC,
        ))
        try:
            self._risk.check_new_position(our_usdc, condition_id)
        except RiskError as e:
            self.emit_alert("warning", f"Risk block: {e}")
            return
        market = await self._client.get_market(condition_id)
        market_name = (market or {}).get("question", condition_id[:20])
        self.emit_alert("info", f"Copying: {market_name[:30]} ${our_usdc:.2f}@{original_price:.3f}")
        resp = await self._executor.buy(
            token_id=token_id, condition_id=condition_id, market_name=market_name,
            outcome="YES", usdc_amount=our_usdc, strategy="copy",
        )
        if not resp:
            self.emit_alert("error", "Copy order failed")
            return
        exec_price = resp.get("price", original_price)
        shares = our_usdc / max(exec_price, 0.001)
        self._notifier.trade_opened("copy", market_name, "BUY", exec_price, our_usdc, paper=cfg.PAPER_TRADE)
        sell_price = self._risk.calculate_sell_price(exec_price, cfg.COPY_AUTO_SELL_PROFIT)
        await self._executor.place_limit(
            token_id=token_id, side="SELL", price=sell_price, shares=shares,
            strategy="copy", condition_id=condition_id, market_name=market_name, outcome="YES",
        )
        self._portfolio.record_trade(TradeRecord(
            trade_id=resp.get("trade_id", str(uuid.uuid4())),
            strategy="copy", market_name=market_name, condition_id=condition_id,
            token_id=token_id, side="BUY", price=exec_price, size=shares,
            total=our_usdc, status=resp.get("status", "PLACED"),
        ))
        self.emit_alert("success", f"Copied! {market_name[:25]} -> sell@{sell_price:.2f}")

    def get_status(self) -> dict:
        return {**super().get_status(), "mode": self._executor.mode,
                "target": cfg.COPY_TRADER_ADDRESS[:12] if cfg.COPY_TRADER_ADDRESS else "not set"}
