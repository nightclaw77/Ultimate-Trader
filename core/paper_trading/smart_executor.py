"""
Smart Executor
Routes BUY/SELL/LIMIT orders to the right execution path based on trading mode:
  PAPER_TRADE=true  -> PaperExecutor (virtual $50, real Polymarket prices)
  DRY_RUN=true      -> log only, no execution
  both false        -> real Polymarket orders
"""
import logging
from typing import Optional

import config as cfg
from core.polymarket.client import get_client
from core.paper_trading.executor import get_paper_executor

logger = logging.getLogger(__name__)


class SmartExecutor:
    """Single interface for all order execution regardless of trading mode."""

    def __init__(self):
        self._paper = get_paper_executor()
        self._client = get_client()

    async def start(self):
        if cfg.PAPER_TRADE:
            await self._paper.start_price_updates()

    async def stop(self):
        if cfg.PAPER_TRADE:
            await self._paper.stop()

    async def buy(
        self,
        token_id: str,
        condition_id: str,
        market_name: str,
        outcome: str,
        usdc_amount: float,
        strategy: str,
    ) -> Optional[dict]:
        if cfg.DRY_RUN:
            logger.info(f"[DRY RUN] BUY ${usdc_amount:.2f} | {market_name[:30]}")
            return {"status": "DRY_RUN", "dry": True}

        if cfg.PAPER_TRADE:
            return await self._paper.paper_buy(
                condition_id=condition_id,
                token_id=token_id,
                market_name=market_name,
                outcome=outcome,
                usdc_amount=usdc_amount,
                strategy=strategy,
            )

        return await self._client.place_market_order(token_id, "BUY", usdc_amount)

    async def sell(
        self,
        position_id: str,
        token_id: str,
        strategy: str,
    ) -> Optional[dict]:
        if cfg.DRY_RUN:
            logger.info(f"[DRY RUN] SELL {position_id}")
            return {"status": "DRY_RUN", "dry": True}

        if cfg.PAPER_TRADE:
            return await self._paper.paper_sell(
                position_id=position_id,
                token_id=token_id,
                strategy=strategy,
            )

        return await self._client.place_market_order(token_id, "SELL", 0)

    async def place_limit(
        self,
        token_id: str,
        side: str,
        price: float,
        shares: float,
        strategy: str,
        condition_id: str = "",
        market_name: str = "",
        outcome: str = "",
    ) -> Optional[dict]:
        if cfg.DRY_RUN:
            logger.info(f"[DRY RUN] LIMIT {side} {shares:.1f}@{price:.3f} | {market_name[:25]}")
            return {"status": "DRY_RUN", "order_id": "dry_limit", "dry": True}

        if cfg.PAPER_TRADE:
            return await self._paper.paper_limit_order(
                token_id=token_id,
                side=side,
                target_price=price,
                shares=shares,
                strategy=strategy,
                condition_id=condition_id,
                market_name=market_name,
                outcome=outcome,
            )

        return await self._client.place_limit_order(token_id, side, price, shares)

    def get_wallet_stats(self) -> Optional[dict]:
        if cfg.PAPER_TRADE:
            return self._paper.get_wallet_stats()
        return None

    @property
    def mode(self) -> str:
        return cfg.trading_mode()
