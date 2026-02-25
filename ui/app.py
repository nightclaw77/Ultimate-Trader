"""
Main Textual TUI Application
"""
import asyncio
import logging
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

import config as cfg
from core.polymarket.client import get_client
from core.risk.portfolio import get_portfolio
from core.strategies.copy_trader import CopyTrader
from core.strategies.market_maker import MarketMaker
from core.strategies.sniper import Sniper
from ui.screens.bot_control import BotControlScreen
from ui.screens.dashboard import DashboardScreen
from ui.screens.markets import MarketsScreen
from ui.screens.positions import PositionsScreen

logger = logging.getLogger(__name__)

TITLE = "Ultimate Trader"
CSS = """
Screen {
    background: $surface;
}

Header {
    background: $primary;
    color: $text;
}

Footer {
    background: $panel;
}

.dry-run-banner {
    background: $warning;
    color: $text;
    text-align: center;
    height: 1;
    text-style: bold;
}
"""


class UltimateTraderApp(App):
    """
    Main TUI Application for Ultimate Trader.
    """

    CSS = CSS
    TITLE = TITLE
    BINDINGS = [
        Binding("1", "switch_tab('dashboard')", "Dashboard", show=True),
        Binding("2", "switch_tab('markets')", "Markets", show=True),
        Binding("3", "switch_tab('positions')", "Positions", show=True),
        Binding("4", "switch_tab('bots')", "Bot Control", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def __init__(self):
        super().__init__()
        self._client = get_client()
        self._portfolio = get_portfolio()
        self._strategies: dict = {}
        self._alerts: list[tuple[str, str, str]] = []  # (time, level, msg)

    def compose(self) -> ComposeResult:
        yield Header()
        if cfg.DRY_RUN:
            from textual.widgets import Static
            yield Static("⚠  DRY RUN MODE - No real trades will be executed  ⚠", classes="dry-run-banner")
        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard [1]", id="dashboard"):
                yield DashboardScreen(self._get_alert_feed)
            with TabPane("Markets [2]", id="markets"):
                yield MarketsScreen()
            with TabPane("Positions [3]", id="positions"):
                yield PositionsScreen()
            with TabPane("Bot Control [4]", id="bots"):
                yield BotControlScreen(self._strategies, self._toggle_strategy)
        yield Footer()

    async def on_mount(self):
        """Initialize strategies and start data refresh."""
        # Initialize strategies
        self._strategies = {
            "copy": CopyTrader(),
            "mm": MarketMaker(),
            "sniper": Sniper(),
        }

        # Register alert handlers
        for name, strategy in self._strategies.items():
            strategy.add_alert_handler(self._handle_alert)

        # Initialize Polymarket client
        try:
            await self._client.initialize()
        except Exception as e:
            self._handle_alert("error", f"[Client] Init failed: {e}")

        # Start background refresh
        self.set_interval(5, self.action_refresh)

    def _handle_alert(self, level: str, message: str):
        """Receive alerts from strategies."""
        ts = datetime.now().strftime("%H:%M:%S")
        self._alerts.insert(0, (ts, level, message))
        self._alerts = self._alerts[:100]  # Keep last 100
        self.call_from_thread(self._broadcast_alert, ts, level, message)

    def _broadcast_alert(self, ts: str, level: str, message: str):
        """Post alert to dashboard in UI thread."""
        try:
            dashboard = self.query_one(DashboardScreen)
            dashboard.add_alert(ts, level, message)
        except Exception:
            pass

    def _get_alert_feed(self) -> list[tuple[str, str, str]]:
        return self._alerts[:30]

    async def _toggle_strategy(self, name: str, enable: bool):
        """Start or stop a strategy."""
        strategy = self._strategies.get(name)
        if not strategy:
            return
        if enable:
            await strategy.start()
        else:
            await strategy.stop()

    def action_switch_tab(self, tab_id: str):
        """Switch to specified tab."""
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            pass

    async def action_refresh(self):
        """Refresh all screens with latest data."""
        try:
            dashboard = self.query_one(DashboardScreen)
            await dashboard.refresh_data()
        except Exception:
            pass

    async def action_quit(self):
        """Graceful shutdown."""
        # Stop all strategies
        for strategy in self._strategies.values():
            if strategy.is_running:
                await strategy.stop()
        await self._client.close()
        self.exit()
