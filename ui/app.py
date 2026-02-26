"""
Main Textual TUI Application - Ultimate Trader
"""
import asyncio
import logging
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

import config as cfg
from core.notifications import get_notifier
from core.paper_trading.smart_executor import SmartExecutor
from core.polymarket.client import get_client
from core.risk.portfolio import get_portfolio
from core.strategies.auto_trader import AutoTrader
from core.strategies.copy_trader import CopyTrader
from core.strategies.market_maker import MarketMaker
from core.strategies.sniper import Sniper
from ui.screens.bot_control import BotControlScreen
from ui.screens.dashboard import DashboardScreen
from ui.screens.markets import MarketsScreen
from ui.screens.positions import PositionsScreen
from ui.screens.trial import TrialScreen

logger = logging.getLogger(__name__)

CSS = """
Screen { background: $surface; }
.dry-run-banner  { background: $warning;  color: $text; text-align: center; height: 1; text-style: bold; }
.paper-banner    { background: $accent;   color: $text; text-align: center; height: 1; text-style: bold; }
.live-banner     { background: $error;    color: $text; text-align: center; height: 1; text-style: bold; }
TabbedContent    { height: 1fr; }
TabPane          { height: 1fr; padding: 0; }
"""


class UltimateTraderApp(App):
    CSS = CSS
    TITLE = "Ultimate Trader"
    BINDINGS = [
        Binding("1", "switch_tab('dashboard')",  "Dashboard",   show=True),
        Binding("2", "switch_tab('markets')",    "Markets",     show=True),
        Binding("3", "switch_tab('positions')",  "Positions",   show=True),
        Binding("4", "switch_tab('bots')",       "Bot Control", show=True),
        Binding("5", "switch_tab('trial')",      "Trial",       show=True),
        Binding("q", "quit",                       "Quit",        show=True),
        Binding("r", "refresh",                    "Refresh",     show=False),
    ]

    def __init__(self):
        super().__init__()
        self._client  = get_client()
        self._portfolio = get_portfolio()
        self._notifier  = get_notifier()
        self._executor  = SmartExecutor()
        self._strategies: dict = {}
        self._alerts: list = []

    def compose(self) -> ComposeResult:
        yield Header()
        mode = cfg.trading_mode()
        if mode == "PAPER":
            yield Static(f"  PAPER TRADE MODE — Virtual ${cfg.PAPER_STARTING_BALANCE:.0f} | No real money used", classes="paper-banner")
        elif mode == "DRY_RUN":
            yield Static("  DRY RUN MODE — Log only, no execution", classes="dry-run-banner")
        else:
            yield Static("  LIVE TRADING — REAL MONEY AT RISK!", classes="live-banner")

        with TabbedContent(initial="dashboard"):
            with TabPane("Dashboard [1]",   id="dashboard"):
                yield DashboardScreen(self._get_alert_feed)
            with TabPane("Markets [2]",     id="markets"):
                yield MarketsScreen()
            with TabPane("Positions [3]",   id="positions"):
                yield PositionsScreen()
            with TabPane("Bot Control [4]", id="bots"):
                yield BotControlScreen(self._strategies, self._toggle_strategy)
            with TabPane("Trial [5]",       id="trial"):
                yield TrialScreen()
        yield Footer()

    async def on_mount(self):
        self._strategies = {
            "auto":   AutoTrader(),
            "copy":   CopyTrader(),
            "mm":     MarketMaker(),
            "sniper": Sniper(),
        }
        for s in self._strategies.values():
            s.add_alert_handler(self._handle_alert)

        # Start support services
        await self._notifier.start()
        await self._executor.start()

        try:
            await self._client.initialize()
        except Exception as e:
            self._handle_alert("error", f"[Client] Init failed: {e}")

        mode = cfg.trading_mode()
        self._notifier.system_alert(f"Ultimate Trader started — Mode: {mode}")

        self.set_interval(5, self.action_refresh)

    def _handle_alert(self, level: str, message: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._alerts.insert(0, (ts, level, message))
        self._alerts = self._alerts[:100]
        self.call_from_thread(self._broadcast_alert, ts, level, message)

    def _broadcast_alert(self, ts, level, message):
        try:
            self.query_one(DashboardScreen).add_alert(ts, level, message)
        except Exception:
            pass

    def _get_alert_feed(self):
        return self._alerts[:30]

    async def _toggle_strategy(self, name: str, enable: bool):
        s = self._strategies.get(name)
        if not s:
            return
        if enable:
            await s.start()
        else:
            await s.stop()

    def action_switch_tab(self, tab_id: str):
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            pass

    async def action_refresh(self):
        try:
            await self.query_one(DashboardScreen).refresh_data()
        except Exception:
            pass

    async def action_quit(self):
        for s in self._strategies.values():
            if s.is_running:
                await s.stop()
        await self._executor.stop()
        await self._notifier.stop()
        await self._client.close()
        self.exit()
