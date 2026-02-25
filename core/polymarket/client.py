"""
Polymarket CLOB Client Wrapper
Based on patterns from:
- polymarket-terminal/src/services/client.js
- nautilus_trader/adapters/polymarket/
"""
import asyncio
import logging
from functools import lru_cache
from typing import Optional

import aiohttp

import config as cfg

logger = logging.getLogger(__name__)

# Signature types (from nautilus_trader analysis)
SIG_TYPE_EOA = 0
SIG_TYPE_POLY_PROXY = 1
SIG_TYPE_GNOSIS_SAFE = 2


class PolymarketClient:
    """
    Async wrapper around py-clob-client for Polymarket API.
    Handles authentication, order placement, and market data.
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._clob = None
        self._initialized = False

    async def initialize(self):
        """Initialize CLOB client with credentials."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds

            creds = ApiCreds(
                api_key=cfg.API_KEY,
                api_secret=cfg.API_SECRET,
                api_passphrase=cfg.API_PASSPHRASE,
            )
            self._clob = ClobClient(
                host=cfg.CLOB_HOST,
                key=cfg.PRIVATE_KEY,
                chain_id=137,
                signature_type=SIG_TYPE_POLY_PROXY,
                funder=cfg.FUNDER_ADDRESS,
                creds=creds,
            )
            self._initialized = True
            logger.info("Polymarket CLOB client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize CLOB client: {e}")
            raise

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={
                    "User-Agent": "UltimateTrader/1.0",
                    # Tell server to use gzip only — avoids brotli decode errors
                    "Accept-Encoding": "gzip, deflate",
                },
            )

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()

    # ---- Balance ----

    async def get_usdc_balance(self) -> float:
        """Get USDC balance of funder address."""
        await self._ensure_session()
        try:
            url = f"{cfg.CLOB_HOST}/balance"
            params = {"address": cfg.FUNDER_ADDRESS}
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get("balance", 0))
        except Exception as e:
            logger.warning(f"Balance fetch failed: {e}")
        return 0.0

    # ---- Market Data ----

    async def get_markets(
        self,
        limit: int = 50,
        offset: int = 0,
        active: bool = True,
        order: str = "volume",
    ) -> list[dict]:
        """Fetch markets from Gamma API sorted by volume."""
        await self._ensure_session()
        try:
            url = f"{cfg.GAMMA_HOST}/markets"
            params = {
                "active": str(active).lower(),
                "limit": limit,
                "offset": offset,
                "order": order,
                "ascending": "false",
            }
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else data.get("markets", [])
                logger.warning(f"Markets fetch HTTP {resp.status}: {await resp.text()}")
        except Exception as e:
            logger.warning(f"Markets fetch failed: {e}")
        return []

    async def get_market(self, condition_id: str) -> Optional[dict]:
        """Get single market by condition ID."""
        await self._ensure_session()
        try:
            url = f"{cfg.GAMMA_HOST}/markets"
            params = {"condition_id": condition_id}
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    markets = data if isinstance(data, list) else []
                    return markets[0] if markets else None
        except Exception as e:
            logger.warning(f"Market fetch failed: {e}")
        return None

    async def get_orderbook(self, token_id: str) -> Optional[dict]:
        """Get order book for a token."""
        await self._ensure_session()
        try:
            url = f"{cfg.CLOB_HOST}/book"
            params = {"token_id": token_id}
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.warning(f"Orderbook fetch failed: {e}")
        return None

    async def get_price(self, token_id: str, side: str = "BUY") -> float:
        """Get best price for token."""
        await self._ensure_session()
        try:
            url = f"{cfg.CLOB_HOST}/price"
            params = {"token_id": token_id, "side": side}
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get("price", 0))
        except Exception as e:
            logger.warning(f"Price fetch failed: {e}")
        return 0.0

    async def search_markets(self, query: str, limit: int = 20) -> list[dict]:
        """Search markets by keyword."""
        await self._ensure_session()
        try:
            url = f"{cfg.GAMMA_HOST}/markets"
            params = {"question": query, "limit": limit, "active": "true"}
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else []
                logger.warning(f"Search HTTP {resp.status}: {await resp.text()}")
        except Exception as e:
            logger.warning(f"Search failed: {e}")
        return []

    async def get_activity(self, address: str, limit: int = 50) -> list[dict]:
        """Get trading activity for a wallet address."""
        await self._ensure_session()
        try:
            url = f"{cfg.DATA_HOST}/activity"
            params = {"user": address, "limit": limit}
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            logger.warning(f"Activity fetch failed: {e}")
        return []

    async def get_positions(self, address: str) -> list[dict]:
        """Get open positions for a wallet."""
        await self._ensure_session()
        try:
            url = f"{cfg.DATA_HOST}/positions"
            params = {"user": address}
            async with self._session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data if isinstance(data, list) else data.get("data", [])
        except Exception as e:
            logger.warning(f"Positions fetch failed: {e}")
        return []

    # ---- Order Placement ----

    async def place_market_order(
        self, token_id: str, side: str, amount: float, slippage: float = 0.05
    ) -> Optional[dict]:
        """Place a market order (FOK - Fill or Kill)."""
        if cfg.DRY_RUN:
            logger.info(
                f"[DRY RUN] Market order: {side} ${amount} of {token_id[:16]}..."
            )
            return {"status": "DRY_RUN", "dry": True}

        if not self._initialized:
            await self.initialize()

        try:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType

            order_args = MarketOrderArgs(
                token_id=token_id,
                amount=amount,
            )
            resp = self._clob.create_and_post_market_order(order_args)
            logger.info(f"Market order placed: {resp}")
            return resp
        except Exception as e:
            logger.error(f"Market order failed: {e}")
            return None

    async def place_limit_order(
        self,
        token_id: str,
        side: str,
        price: float,
        size: float,
        expiration: int = 0,
    ) -> Optional[dict]:
        """Place a GTC limit order."""
        if cfg.DRY_RUN:
            logger.info(
                f"[DRY RUN] Limit order: {side} {size}@{price} of {token_id[:16]}..."
            )
            return {"status": "DRY_RUN", "order_id": f"dry_{token_id[:8]}_{side}", "dry": True}

        if not self._initialized:
            await self.initialize()

        try:
            from py_clob_client.clob_types import LimitOrderArgs, OrderType

            order_args = LimitOrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side,
                expiration=expiration,
            )
            signed_order = self._clob.create_order(order_args)
            resp = self._clob.post_order(signed_order, OrderType.GTC)
            logger.info(f"Limit order placed: {resp}")
            return resp
        except Exception as e:
            logger.error(f"Limit order failed: {e}")
            return None

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        if cfg.DRY_RUN:
            logger.info(f"[DRY RUN] Cancel order: {order_id}")
            return True

        if not self._initialized:
            await self.initialize()

        try:
            resp = self._clob.cancel({"orderID": order_id})
            return bool(resp)
        except Exception as e:
            logger.error(f"Cancel order failed: {e}")
            return False

    async def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        if cfg.DRY_RUN:
            logger.info("[DRY RUN] Cancel all orders")
            return True

        if not self._initialized:
            await self.initialize()

        try:
            self._clob.cancel_all()
            return True
        except Exception as e:
            logger.error(f"Cancel all orders failed: {e}")
            return False

    async def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        if not self._initialized:
            await self.initialize()

        try:
            orders = self._clob.get_orders()
            return orders if isinstance(orders, list) else []
        except Exception as e:
            logger.warning(f"Get open orders failed: {e}")
        return []


# Singleton
_client: Optional[PolymarketClient] = None


def get_client() -> PolymarketClient:
    global _client
    if _client is None:
        _client = PolymarketClient()
    return _client
