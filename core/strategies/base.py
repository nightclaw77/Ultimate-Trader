"""
Base Strategy Abstract Class
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Callable, Optional

import config as cfg
from core.polymarket.client import get_client
from core.risk.manager import get_risk_manager
from core.risk.portfolio import get_portfolio

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """
    Abstract base for all trading strategies.
    Provides common infrastructure: client, portfolio, risk manager, event bus.
    """

    def __init__(self, name: str):
        self.name = name
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._alerts: list[Callable[[str, str], None]] = []  # (level, message)
        self._client = get_client()
        self._portfolio = get_portfolio()
        self._risk = get_risk_manager()

    def add_alert_handler(self, handler: Callable[[str, str], None]):
        """Register a callback for strategy alerts (used by TUI)."""
        self._alerts.append(handler)

    def emit_alert(self, level: str, message: str):
        """Send an alert to all registered handlers."""
        for handler in self._alerts:
            try:
                handler(level, f"[{self.name}] {message}")
            except Exception:
                pass

    async def start(self):
        """Start strategy in background."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        self.emit_alert("info", "Started")
        logger.info(f"Strategy {self.name} started")

    async def stop(self):
        """Stop strategy gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.on_stop()
        self.emit_alert("info", "Stopped")
        logger.info(f"Strategy {self.name} stopped")

    async def _run_loop(self):
        """Main strategy loop with error recovery."""
        try:
            await self.on_start()
            while self._running:
                try:
                    await self.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"{self.name} error: {e}")
                    self.emit_alert("error", str(e))
                    await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        finally:
            await self.on_stop()

    @abstractmethod
    async def on_start(self):
        """Called when strategy starts."""
        pass

    @abstractmethod
    async def on_stop(self):
        """Called when strategy stops."""
        pass

    @abstractmethod
    async def run_once(self):
        """Main strategy logic, called in loop."""
        pass

    @property
    def is_running(self) -> bool:
        return self._running

    def get_status(self) -> dict:
        return {
            "name": self.name,
            "running": self._running,
            "dry_run": cfg.DRY_RUN,
        }
