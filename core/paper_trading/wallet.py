"""
Virtual Wallet for Paper Trading (Trial Mode)

Simulates a real trading account with $50 USDC starting balance.
- Uses REAL Polymarket market data for prices
- Executes trades at ACTUAL current market prices
- Tracks real P&L based on live price movements
- No real money involved — pure simulation

This gives a realistic performance assessment before going live.
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional
from pathlib import Path

import config as cfg

logger = logging.getLogger(__name__)

PAPER_WALLET_FILE = cfg.DATA_DIR / "paper_wallet.json"
STARTING_BALANCE = 50.0  # $50 virtual USDC


@dataclass
class PaperPosition:
    """A virtual position in a Polymarket market."""
    position_id: str           # condition_id-token_id
    condition_id: str
    token_id: str
    market_name: str
    outcome: str               # YES / NO
    shares: float
    entry_price: float         # Real price at time of "purchase"
    total_cost: float          # shares * entry_price
    strategy: str              # copy/mm/sniper
    current_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    status: str = "open"       # open / closed / redeemed
    opened_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    closed_at: Optional[str] = None
    close_price: Optional[float] = None

    def update_price(self, new_price: float):
        """Update P&L based on current market price."""
        self.current_price = new_price
        self.pnl = (new_price - self.entry_price) * self.shares
        if self.entry_price > 0:
            self.pnl_pct = (new_price - self.entry_price) / self.entry_price * 100

    def close(self, close_price: float) -> float:
        """Close position and return realized P&L."""
        self.close_price = close_price
        self.pnl = (close_price - self.entry_price) * self.shares
        self.status = "closed"
        self.closed_at = datetime.utcnow().isoformat()
        return self.pnl


@dataclass
class PaperTrade:
    """Record of a paper trade execution."""
    trade_id: str
    strategy: str
    market_name: str
    side: str                  # BUY / SELL
    shares: float
    price: float               # Actual market price used
    total: float               # shares * price
    balance_before: float
    balance_after: float
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class PaperWallet:
    """
    Virtual trading wallet for paper trading simulation.

    Features:
    - Starts with $50 virtual USDC
    - All "trades" execute at real Polymarket prices
    - Tracks unrealized P&L in real-time using live prices
    - Tracks realized P&L from closed positions
    - Full trade history
    - Persistent across restarts (saved to JSON)
    """

    def __init__(self):
        self._balance: float = STARTING_BALANCE
        self._initial_balance: float = STARTING_BALANCE
        self._positions: dict[str, PaperPosition] = {}
        self._trades: list[PaperTrade] = []
        self._realized_pnl: float = 0.0
        self._load()

    def _load(self):
        """Load paper wallet state from disk."""
        try:
            if PAPER_WALLET_FILE.exists():
                with open(PAPER_WALLET_FILE) as f:
                    data = json.load(f)
                self._balance = data.get("balance", STARTING_BALANCE)
                self._initial_balance = data.get("initial_balance", STARTING_BALANCE)
                self._realized_pnl = data.get("realized_pnl", 0.0)
                for k, v in data.get("positions", {}).items():
                    self._positions[k] = PaperPosition(**v)
                self._trades = [PaperTrade(**t) for t in data.get("trades", [])]
                logger.info(
                    f"Paper wallet loaded: ${self._balance:.2f} USDC, "
                    f"{len(self._positions)} open positions"
                )
        except Exception as e:
            logger.warning(f"Paper wallet load error (starting fresh with ${STARTING_BALANCE}): {e}")

    def _save(self):
        """Persist wallet state to disk."""
        try:
            with open(PAPER_WALLET_FILE, "w") as f:
                json.dump(
                    {
                        "balance": self._balance,
                        "initial_balance": self._initial_balance,
                        "realized_pnl": self._realized_pnl,
                        "positions": {k: asdict(v) for k, v in self._positions.items()},
                        "trades": [asdict(t) for t in self._trades[-500:]],
                        "last_updated": datetime.utcnow().isoformat(),
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.error(f"Paper wallet save error: {e}")

    # ---- Core Operations ----

    def can_buy(self, amount_usdc: float) -> tuple[bool, str]:
        """Check if we can afford this purchase."""
        if amount_usdc > self._balance:
            return False, f"Insufficient balance: ${self._balance:.2f} < ${amount_usdc:.2f}"
        if amount_usdc < 0.01:
            return False, "Amount too small"
        return True, ""

    def execute_buy(
        self,
        condition_id: str,
        token_id: str,
        market_name: str,
        outcome: str,
        shares: float,
        price: float,         # Real Polymarket price
        strategy: str,
        trade_id: str,
    ) -> Optional[PaperPosition]:
        """
        Execute a virtual BUY at the real market price.
        Deducts from virtual USDC balance.
        """
        cost = shares * price
        ok, reason = self.can_buy(cost)
        if not ok:
            logger.warning(f"Paper buy rejected: {reason}")
            return None

        position_id = f"{condition_id}-{token_id}"
        balance_before = self._balance
        self._balance -= cost

        position = PaperPosition(
            position_id=position_id,
            condition_id=condition_id,
            token_id=token_id,
            market_name=market_name,
            outcome=outcome,
            shares=shares,
            entry_price=price,
            total_cost=cost,
            strategy=strategy,
            current_price=price,
        )

        # If position already exists, average in
        if position_id in self._positions:
            existing = self._positions[position_id]
            total_shares = existing.shares + shares
            avg_price = (existing.total_cost + cost) / total_shares
            existing.shares = total_shares
            existing.entry_price = avg_price
            existing.total_cost = total_shares * avg_price
            position = existing
        else:
            self._positions[position_id] = position

        self._trades.append(PaperTrade(
            trade_id=trade_id,
            strategy=strategy,
            market_name=market_name,
            side="BUY",
            shares=shares,
            price=price,
            total=cost,
            balance_before=balance_before,
            balance_after=self._balance,
        ))
        self._save()

        logger.info(
            f"[PAPER] BUY {shares:.1f}x {market_name[:20]} @${price:.3f} "
            f"cost=${cost:.2f} | Balance: ${self._balance:.2f}"
        )
        return position

    def execute_sell(
        self,
        position_id: str,
        price: float,
        strategy: str,
        trade_id: str,
    ) -> float:
        """
        Execute a virtual SELL at the real market price.
        Returns realized P&L.
        """
        if position_id not in self._positions:
            logger.warning(f"Paper sell: position {position_id} not found")
            return 0.0

        pos = self._positions[position_id]
        balance_before = self._balance
        proceeds = pos.shares * price
        realized_pnl = pos.close(price)

        self._balance += proceeds
        self._realized_pnl += realized_pnl

        self._trades.append(PaperTrade(
            trade_id=trade_id,
            strategy=strategy,
            market_name=pos.market_name,
            side="SELL",
            shares=pos.shares,
            price=price,
            total=proceeds,
            balance_before=balance_before,
            balance_after=self._balance,
        ))

        del self._positions[position_id]
        self._save()

        pnl_sign = "+" if realized_pnl >= 0 else ""
        logger.info(
            f"[PAPER] SELL {pos.market_name[:20]} @${price:.3f} "
            f"P&L={pnl_sign}${realized_pnl:.2f} | Balance: ${self._balance:.2f}"
        )
        return realized_pnl

    def update_prices(self, prices: dict[str, float]):
        """
        Update unrealized P&L for all open positions.
        Call this regularly with real Polymarket prices.

        Args:
            prices: {token_id: current_price}
        """
        for pos in self._positions.values():
            if pos.token_id in prices:
                pos.update_price(prices[pos.token_id])

    def reset(self, new_balance: float = STARTING_BALANCE):
        """Reset paper wallet to fresh state."""
        self._balance = new_balance
        self._initial_balance = new_balance
        self._positions = {}
        self._trades = []
        self._realized_pnl = 0.0
        self._save()
        logger.info(f"Paper wallet reset to ${new_balance}")

    # ---- Metrics ----

    @property
    def balance(self) -> float:
        return self._balance

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.pnl for p in self._positions.values())

    @property
    def realized_pnl(self) -> float:
        return self._realized_pnl

    @property
    def total_pnl(self) -> float:
        return self._realized_pnl + self.unrealized_pnl

    @property
    def total_return_pct(self) -> float:
        if self._initial_balance <= 0:
            return 0.0
        return self.total_pnl / self._initial_balance * 100

    @property
    def open_positions(self) -> list[PaperPosition]:
        return list(self._positions.values())

    @property
    def trade_count(self) -> int:
        return len([t for t in self._trades if t.side == "BUY"])

    @property
    def win_rate(self) -> float:
        sells = [t for t in self._trades if t.side == "SELL"]
        if not sells:
            return 0.0
        # A sell is a "win" if the position made profit
        wins = 0
        for sell in sells:
            # Find matching buy
            buys = [t for t in self._trades if t.side == "BUY" and t.market_name == sell.market_name]
            if buys:
                avg_buy = sum(b.price for b in buys) / len(buys)
                if sell.price > avg_buy:
                    wins += 1
        return wins / len(sells) * 100 if sells else 0.0

    def get_stats(self) -> dict:
        return {
            "balance": self._balance,
            "initial_balance": self._initial_balance,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self._realized_pnl,
            "total_pnl": self.total_pnl,
            "total_return_pct": self.total_return_pct,
            "open_positions": len(self._positions),
            "trade_count": self.trade_count,
            "win_rate": self.win_rate,
        }

    def get_recent_trades(self, limit: int = 50) -> list[PaperTrade]:
        return list(reversed(self._trades[-limit:]))


# Singleton
_paper_wallet: Optional[PaperWallet] = None


def get_paper_wallet() -> PaperWallet:
    global _paper_wallet
    if _paper_wallet is None:
        _paper_wallet = PaperWallet()
    return _paper_wallet
