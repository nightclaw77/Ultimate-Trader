"""
Dashboard Screen - Main TUI screen with live market data, positions, and alerts.
"""
import asyncio
from datetime import datetime
from typing import Callable

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Label, RichLog, Static

import config as cfg
from core.polymarket.client import get_client
from core.risk.portfolio import get_portfolio

LEVEL_COLORS = {
    "success": "green",
    "info": "cyan",
    "warning": "yellow",
    "error": "red",
}


class DashboardScreen(Widget):
    """
    Main dashboard: 3-column layout
    Left: Top markets
    Middle: Active positions + P&L summary
    Right: Live alerts feed
    Bottom: Bot status bar
    """

    DEFAULT_CSS = """
    DashboardScreen {
        layout: grid;
        grid-size: 3 3;
        grid-rows: 1fr 1fr 4;
        grid-columns: 2fr 1fr 2fr;
    }

    .panel {
        border: solid $primary;
        padding: 0 1;
    }

    .panel-title {
        background: $primary;
        color: $text;
        text-align: center;
        text-style: bold;
    }

    .status-bar {
        column-span: 3;
        background: $panel;
        border: solid $accent;
        padding: 0 1;
        layout: horizontal;
    }

    .alert-log {
        column-span: 1;
        row-span: 2;
    }

    .market-table {
        column-span: 1;
        row-span: 2;
    }

    .positions-panel {
        column-span: 1;
        row-span: 1;
    }

    .pnl-panel {
        column-span: 1;
        row-span: 1;
    }
    """

    def __init__(self, alert_feed_getter: Callable):
        super().__init__()
        self._alert_getter = alert_feed_getter
        self._client = get_client()
        self._portfolio = get_portfolio()

    def compose(self) -> ComposeResult:
        # Markets table (left)
        yield Static("TOP MARKETS", classes="panel-title")
        yield DataTable(id="market-table", classes="panel market-table")

        # Positions (middle top)
        yield Static("ACTIVE POSITIONS", classes="panel-title")
        yield DataTable(id="position-table", classes="panel positions-panel")

        # Alerts feed (right)
        yield Static("LIVE ALERTS", classes="panel-title")
        yield RichLog(id="alert-log", classes="panel alert-log", highlight=True, markup=True)

        # P&L summary (middle bottom)
        yield Static("P&L SUMMARY", classes="panel-title")
        yield Static("Loading...", id="pnl-summary", classes="panel pnl-panel")

        # Status bar (full width bottom)
        yield Static(id="status-bar", classes="status-bar")

    async def on_mount(self):
        """Initialize tables."""
        market_table = self.query_one("#market-table", DataTable)
        market_table.add_columns("Market", "Prob", "Volume", "Change")

        pos_table = self.query_one("#position-table", DataTable)
        pos_table.add_columns("Market", "Side", "P&L", "Strategy")

        await self.refresh_data()

    async def refresh_data(self):
        """Fetch and update all dashboard data."""
        try:
            await self._update_markets()
            self._update_positions()
            self._update_pnl()
            self._update_status_bar()
        except Exception as e:
            pass

    async def _update_markets(self):
        """Refresh top markets table."""
        try:
            markets = await self._client.get_markets(limit=20, order="volume_num")
            table = self.query_one("#market-table", DataTable)
            table.clear()

            for m in markets[:15]:
                question = m.get("question") or m.get("title", "")
                tokens = m.get("tokens") or []
                prob = "?"
                if tokens:
                    yes_price = tokens[0].get("price") if tokens else 0
                    if yes_price:
                        prob = f"{float(yes_price)*100:.0f}%"

                volume = m.get("volume") or m.get("volume_num", 0)
                if volume:
                    if float(volume) >= 1_000_000:
                        vol_str = f"${float(volume)/1_000_000:.1f}M"
                    elif float(volume) >= 1_000:
                        vol_str = f"${float(volume)/1_000:.0f}K"
                    else:
                        vol_str = f"${float(volume):.0f}"
                else:
                    vol_str = "-"

                table.add_row(
                    question[:30] + ("..." if len(question) > 30 else ""),
                    prob,
                    vol_str,
                    "→",
                )
        except Exception:
            pass

    def _update_positions(self):
        """Refresh active positions table."""
        table = self.query_one("#position-table", DataTable)
        table.clear()
        positions = self._portfolio.get_open_positions()

        if not positions:
            table.add_row("No open positions", "", "", "")
            return

        for pos in positions:
            pnl_str = f"+${pos.pnl:.2f}" if pos.pnl >= 0 else f"-${abs(pos.pnl):.2f}"
            table.add_row(
                pos.market_name[:20],
                pos.outcome,
                pnl_str,
                pos.strategy,
            )

    def _update_pnl(self):
        """Update P&L summary."""
        stats = self._portfolio.get_stats()
        daily = stats["daily_pnl"]
        total = stats["total_pnl"]
        win_rate = stats["win_rate"]

        daily_color = "green" if daily >= 0 else "red"
        total_color = "green" if total >= 0 else "red"

        text = (
            f"[bold]Daily P&L:[/bold] [{daily_color}]${daily:+.2f}[/{daily_color}]   "
            f"[bold]Total:[/bold] [{total_color}]${total:+.2f}[/{total_color}]   "
            f"[bold]Win Rate:[/bold] {win_rate:.0f}%   "
            f"[bold]Open:[/bold] {stats['open_positions']}"
        )
        try:
            self.query_one("#pnl-summary", Static).update(text)
        except Exception:
            pass

    def _update_status_bar(self):
        """Update bottom status bar with bot states."""
        dry = "[yellow]DRY RUN[/yellow]" if cfg.DRY_RUN else "[green]LIVE[/green]"
        text = (
            f" {dry}  |  "
            f"Max Position: ${cfg.MAX_POSITION_USDC}  |  "
            f"Daily Limit: ${cfg.DAILY_LOSS_LIMIT}  |  "
            f"{datetime.now().strftime('%H:%M:%S')}"
        )
        try:
            self.query_one("#status-bar", Static).update(text)
        except Exception:
            pass

    def add_alert(self, ts: str, level: str, message: str):
        """Add new alert to the log."""
        color = LEVEL_COLORS.get(level, "white")
        icons = {"success": "✓", "info": "ℹ", "warning": "⚠", "error": "✗"}
        icon = icons.get(level, "•")
        try:
            log = self.query_one("#alert-log", RichLog)
            log.write(f"[dim]{ts}[/dim] [{color}]{icon} {message}[/{color}]")
        except Exception:
            pass
