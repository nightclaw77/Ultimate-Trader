"""
Portfolio State Manager
JSON-based persistence (from polymarket-terminal/src/services/position.js)
Data structures inspired by nautilus_trader/adapters/polymarket/schemas/
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

import config as cfg

logger = logging.getLogger(__name__)


@dataclass
class MarketPosition:
    """
    Active position in a Polymarket market.
    Based on polymarket-terminal position structure.
    """
    condition_id: str
    token_id: str
    market_name: str
    outcome: str           # YES or NO
    shares: float
    avg_buy_price: float
    total_cost: float
    strategy: str          # copy/mm/sniper
    status: str = "open"   # open/selling/sold/redeemed
    sell_order_id: Optional[str] = None
    pnl: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    @property
    def position_id(self) -> str:
        return f"{self.condition_id}-{self.token_id}"

    def update_pnl(self, current_price: float):
        self.pnl = (current_price - self.avg_buy_price) * self.shares
        self.updated_at = datetime.utcnow().isoformat()


@dataclass
class TradeRecord:
    """Historical trade record."""
    trade_id: str
    strategy: str
    market_name: str
    condition_id: str
    token_id: str
    side: str              # BUY or SELL
    price: float
    size: float
    total: float
    status: str            # MATCHED/MINED/CONFIRMED/DRY_RUN
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


class Portfolio:
    """
    Manages positions and trade history with JSON persistence.
    Thread-safe via asyncio (single-threaded event loop).
    """

    def __init__(self):
        self._positions: dict[str, MarketPosition] = {}
        self._trades: list[TradeRecord] = []
        self._daily_pnl: float = 0.0
        self._total_pnl: float = 0.0
        self._load()

    def _load(self):
        """Load state from disk."""
        try:
            if cfg.POSITIONS_FILE.exists():
                with open(cfg.POSITIONS_FILE) as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self._positions[k] = MarketPosition(**v)
            if cfg.TRADES_FILE.exists():
                with open(cfg.TRADES_FILE) as f:
                    data = json.load(f)
                    self._trades = [TradeRecord(**t) for t in data.get("trades", [])]
                    self._daily_pnl = data.get("daily_pnl", 0.0)
                    self._total_pnl = data.get("total_pnl", 0.0)
            logger.info(
                f"Portfolio loaded: {len(self._positions)} positions, "
                f"{len(self._trades)} trades"
            )
        except Exception as e:
            logger.warning(f"Portfolio load error (starting fresh): {e}")

    def _save(self):
        """Persist state to disk."""
        try:
            with open(cfg.POSITIONS_FILE, "w") as f:
                json.dump(
                    {k: asdict(v) for k, v in self._positions.items()},
                    f,
                    indent=2,
                )
            with open(cfg.TRADES_FILE, "w") as f:
                json.dump(
                    {
                        "trades": [asdict(t) for t in self._trades[-1000:]],
                        "daily_pnl": self._daily_pnl,
                        "total_pnl": self._total_pnl,
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logger.error(f"Portfolio save error: {e}")

    # ---- Positions ----

    def add_position(self, position: MarketPosition):
        self._positions[position.position_id] = position
        self._save()
        logger.info(
            f"Position added: {position.market_name} {position.outcome} "
            f"x{position.shares} @{position.avg_buy_price}"
        )

    def update_position(self, position_id: str, **kwargs):
        if position_id in self._positions:
            pos = self._positions[position_id]
            for k, v in kwargs.items():
                if hasattr(pos, k):
                    setattr(pos, k, v)
            pos.updated_at = datetime.utcnow().isoformat()
            self._save()

    def close_position(self, position_id: str, close_price: float):
        if position_id in self._positions:
            pos = self._positions[position_id]
            pos.pnl = (close_price - pos.avg_buy_price) * pos.shares
            pos.status = "sold"
            self._daily_pnl += pos.pnl
            self._total_pnl += pos.pnl
            del self._positions[position_id]
            self._save()
            return pos.pnl
        return 0.0

    def get_open_positions(self) -> list[MarketPosition]:
        return [p for p in self._positions.values() if p.status == "open"]

    def get_positions_by_strategy(self, strategy: str) -> list[MarketPosition]:
        return [p for p in self._positions.values() if p.strategy == strategy]

    # ---- Trades ----

    def record_trade(self, trade: TradeRecord):
        self._trades.append(trade)
        self._save()

    def get_recent_trades(self, limit: int = 50) -> list[TradeRecord]:
        return self._trades[-limit:]

    # ---- Metrics ----

    @property
    def open_position_count(self) -> int:
        return len([p for p in self._positions.values() if p.status == "open"])

    @property
    def total_invested(self) -> float:
        return sum(p.total_cost for p in self._positions.values() if p.status == "open")

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def total_pnl(self) -> float:
        return self._total_pnl

    @property
    def win_rate(self) -> float:
        closed = [t for t in self._trades if t.side == "SELL"]
        if not closed:
            return 0.0
        wins = sum(1 for t in closed if t.total > 0)
        return wins / len(closed) * 100

    def reset_daily_pnl(self):
        self._daily_pnl = 0.0
        self._save()

    def get_stats(self) -> dict:
        return {
            "open_positions": self.open_position_count,
            "total_invested": self.total_invested,
            "daily_pnl": self.daily_pnl,
            "total_pnl": self.total_pnl,
            "win_rate": self.win_rate,
            "total_trades": len(self._trades),
        }


# Singleton
_portfolio: Optional[Portfolio] = None


def get_portfolio() -> Portfolio:
    global _portfolio
    if _portfolio is None:
        _portfolio = Portfolio()
    return _portfolio
