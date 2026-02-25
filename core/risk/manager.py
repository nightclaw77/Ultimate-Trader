"""
Risk Manager
Position sizing, balance guards, daily limits.
Patterns from polymarket-terminal/src/services/executor.js
and trader project's risk_manager.py
"""
import logging
from typing import Optional

import config as cfg
from core.risk.portfolio import get_portfolio

logger = logging.getLogger(__name__)


class RiskError(Exception):
    """Raised when a trade would violate risk limits."""
    pass


class RiskManager:
    """
    Validates orders against risk parameters before execution.
    """

    def __init__(self):
        self._portfolio = get_portfolio()

    def check_new_position(
        self,
        amount_usdc: float,
        market_name: str = "",
    ) -> None:
        """
        Validate that opening a new position is safe.
        Raises RiskError if limits would be breached.
        """
        # Check balance guard (from polymarket-terminal executor)
        if amount_usdc < 0.01:
            raise RiskError(f"Amount too small: ${amount_usdc}")

        if amount_usdc > cfg.MAX_POSITION_USDC:
            raise RiskError(
                f"Position ${amount_usdc} exceeds max ${cfg.MAX_POSITION_USDC}"
            )

        # Max open positions
        if self._portfolio.open_position_count >= cfg.MAX_OPEN_POSITIONS:
            raise RiskError(
                f"Max positions reached ({cfg.MAX_OPEN_POSITIONS})"
            )

        # Daily loss limit
        if self._portfolio.daily_pnl < -cfg.DAILY_LOSS_LIMIT:
            raise RiskError(
                f"Daily loss limit hit (${self._portfolio.daily_pnl:.2f})"
            )

        # Total capital at risk
        if self._portfolio.total_invested + amount_usdc > cfg.MAX_POSITION_USDC * cfg.MAX_OPEN_POSITIONS:
            raise RiskError("Total capital at risk limit exceeded")

    def calculate_position_size(
        self,
        available_usdc: float,
        confidence: float = 1.0,
        strategy: str = "default",
    ) -> float:
        """
        Calculate safe position size based on available capital and confidence.

        Args:
            available_usdc: Available USDC balance
            confidence: 0.0-1.0 confidence score
            strategy: Strategy name for sizing rules

        Returns:
            Position size in USDC
        """
        base_size = min(cfg.MAX_POSITION_USDC, available_usdc * 0.10)

        # Strategy-specific sizing
        if strategy == "sniper":
            # Sniper uses small amounts (from sniper analysis: $0.01*shares*2)
            base_size = min(base_size, cfg.SNIPER_PRICE * cfg.SNIPER_SHARES * 2)
        elif strategy == "mm":
            base_size = min(base_size, cfg.MM_TRADE_SIZE)
        elif strategy == "copy":
            base_size = min(
                base_size,
                cfg.MAX_POSITION_USDC * (cfg.COPY_SIZE_PERCENT / 100),
            )

        # Scale by confidence
        size = base_size * max(0.1, confidence)
        return round(max(0.10, min(size, cfg.MAX_POSITION_USDC)), 2)

    def calculate_sell_price(self, avg_buy_price: float, profit_pct: float) -> float:
        """
        Calculate auto-sell price for profit target.
        From polymarket-terminal/src/services/autoSell.js
        """
        sell = avg_buy_price * (1 + profit_pct / 100)
        # Constrain to valid Polymarket price range [0.01, 0.99]
        sell = max(0.01, min(0.99, sell))
        # Round to tick size (0.01)
        return round(sell, 2)

    def should_cut_loss(self, current_price: float, avg_buy_price: float, loss_pct: float = 50) -> bool:
        """Check if position should be cut to prevent total loss."""
        loss = (avg_buy_price - current_price) / avg_buy_price * 100
        return loss >= loss_pct

    def get_status(self) -> dict:
        stats = self._portfolio.get_stats()
        return {
            **stats,
            "daily_limit": cfg.DAILY_LOSS_LIMIT,
            "max_position": cfg.MAX_POSITION_USDC,
            "dry_run": cfg.DRY_RUN,
            "limit_ok": self._portfolio.daily_pnl > -cfg.DAILY_LOSS_LIMIT,
        }


# Singleton
_risk_manager: Optional[RiskManager] = None


def get_risk_manager() -> RiskManager:
    global _risk_manager
    if _risk_manager is None:
        _risk_manager = RiskManager()
    return _risk_manager
