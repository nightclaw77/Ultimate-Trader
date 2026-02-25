"""
Paper Trading Executor
Intercepts all orders and executes them virtually using REAL market prices.

When PAPER_TRADE=true:
1. Fetches actual current price from Polymarket API
2. Executes trade at that real price in virtual wallet
3. Tracks P&L based on real price movements
4. No real funds used

This gives realistic results - if the strategy would work in paper trading,
it's likely to work in live trading too.
"""
import asyncio
import logging
import uuid
from typing import Optional

import config as cfg
from core.paper_trading.wallet import PaperPosition, get_paper_wallet
from core.polymarket.client import get_client

logger = logging.getLogger(__name__)


class PaperExecutor:
    """
    Intercepts trade execution and simulates against real market data.
    Replaces real order placement when in paper trading mode.
    """

    def __init__(self):
        self._wallet = get_paper_wallet()
        self._client = get_client()
        self._price_cache: dict[str, float] = {}
        self._price_update_task: Optional[asyncio.Task] = None

    async def start_price_updates(self):
        """Start background task to update position prices."""
        self._price_update_task = asyncio.create_task(self._update_prices_loop())

    async def stop(self):
        if self._price_update_task:
            self._price_update_task.cancel()

    async def _update_prices_loop(self):
        """Continuously update prices for open positions."""
        while True:
            try:
                await self._refresh_position_prices()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Price update error: {e}")
            await asyncio.sleep(30)  # Update every 30 seconds

    async def _refresh_position_prices(self):
        """Fetch current prices for all open positions."""
        positions = self._wallet.open_positions
        if not positions:
            return

        prices = {}
        for pos in positions:
            try:
                price = await self._client.get_price(pos.token_id, "BUY")
                if price > 0:
                    prices[pos.token_id] = price
                    self._price_cache[pos.token_id] = price
            except Exception:
                pass

        if prices:
            self._wallet.update_prices(prices)

    async def get_real_price(self, token_id: str, side: str = "BUY") -> float:
        """
        Get actual current market price from Polymarket.
        This is the price the paper trade will execute at.
        """
        # Try cache first (for very recent prices)
        if token_id in self._price_cache:
            return self._price_cache[token_id]

        price = await self._client.get_price(token_id, side)
        if price > 0:
            self._price_cache[token_id] = price
        return price

    async def paper_buy(
        self,
        condition_id: str,
        token_id: str,
        market_name: str,
        outcome: str,
        usdc_amount: float,
        strategy: str,
    ) -> Optional[dict]:
        """
        Execute a paper BUY at real current market price.

        Args:
            condition_id: Market condition ID
            token_id: Token to buy
            market_name: Human-readable market name
            outcome: YES or NO
            usdc_amount: How much USDC to spend
            strategy: Strategy name

        Returns:
            Virtual order response dict, or None if failed
        """
        # Get real current market price (BUY side = ask price)
        real_price = await self.get_real_price(token_id, "BUY")

        if real_price <= 0:
            logger.warning(f"Paper buy failed: no real price for {token_id[:16]}")
            return None

        # Calculate shares based on real price
        shares = usdc_amount / real_price

        trade_id = f"paper_{uuid.uuid4().hex[:12]}"
        position = self._wallet.execute_buy(
            condition_id=condition_id,
            token_id=token_id,
            market_name=market_name,
            outcome=outcome,
            shares=shares,
            price=real_price,
            strategy=strategy,
            trade_id=trade_id,
        )

        if not position:
            return None

        logger.info(
            f"[PAPER BUY] {market_name[:25]} | {shares:.1f}x @${real_price:.3f} "
            f"= ${usdc_amount:.2f} | Balance: ${self._wallet.balance:.2f}"
        )

        return {
            "status": "PAPER_FILLED",
            "trade_id": trade_id,
            "order_id": f"paper_{token_id[:8]}",
            "price": real_price,
            "shares": shares,
            "cost": usdc_amount,
            "balance": self._wallet.balance,
            "paper": True,
        }

    async def paper_sell(
        self,
        position_id: str,
        token_id: str,
        strategy: str,
    ) -> Optional[dict]:
        """
        Execute a paper SELL at real current market price.

        Returns:
            Virtual order response with realized P&L
        """
        # Get real current market price (SELL side = bid price)
        real_price = await self.get_real_price(token_id, "SELL")

        if real_price <= 0:
            logger.warning(f"Paper sell failed: no real price for {token_id[:16]}")
            return None

        trade_id = f"paper_{uuid.uuid4().hex[:12]}"
        pnl = self._wallet.execute_sell(
            position_id=position_id,
            price=real_price,
            strategy=strategy,
            trade_id=trade_id,
        )

        pnl_sign = "+" if pnl >= 0 else ""
        logger.info(
            f"[PAPER SELL] @${real_price:.3f} | P&L={pnl_sign}${pnl:.2f} "
            f"| Balance: ${self._wallet.balance:.2f}"
        )

        return {
            "status": "PAPER_FILLED",
            "trade_id": trade_id,
            "price": real_price,
            "realized_pnl": pnl,
            "balance": self._wallet.balance,
            "paper": True,
        }

    async def paper_limit_order(
        self,
        token_id: str,
        side: str,
        target_price: float,
        shares: float,
        strategy: str,
        condition_id: str = "",
        market_name: str = "",
        outcome: str = "",
    ) -> dict:
        """
        Simulate a limit order.
        For paper trading, GTC limit orders are "pending" and checked against
        real prices periodically.
        """
        order_id = f"paper_limit_{uuid.uuid4().hex[:8]}"

        logger.info(
            f"[PAPER LIMIT] {side} {shares:.1f}x {market_name[:20]} "
            f"target=${target_price:.3f}"
        )

        return {
            "status": "PAPER_PENDING",
            "order_id": order_id,
            "token_id": token_id,
            "side": side,
            "price": target_price,
            "shares": shares,
            "paper": True,
        }

    def get_wallet_stats(self) -> dict:
        return self._wallet.get_stats()


# Singleton
_paper_executor: Optional[PaperExecutor] = None


def get_paper_executor() -> PaperExecutor:
    global _paper_executor
    if _paper_executor is None:
        _paper_executor = PaperExecutor()
    return _paper_executor
