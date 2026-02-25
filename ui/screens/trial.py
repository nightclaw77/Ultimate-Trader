"""
Trial Mode Screen — Paper Trading Dashboard

Shows the $50 virtual wallet performance using real Polymarket prices.
P&L is updated every 30 seconds from live market data.

Layout:
┌─────────────── PAPER TRADING — $50 Virtual Account ──────────────┐
│  💰 Balance: $47.30   📈 Total P&L: +$2.30 (+4.6%)              │
│  🎯 Win Rate: 67%     📊 Trades: 12    Open Positions: 3         │
├───────────────────────────────────────────────────────────────────┤
│  OPEN POSITIONS (P&L updates every 30s from real prices)         │
│  Market              Out    Shares  Entry   Now    P&L   Strategy│
│  BTC>100k updown     YES    50x    $0.02  $0.15  +$6.50 sniper  │
│  ETH>5k Jul25        YES    25x    $0.48  $0.46  -$0.50 mm      │
├───────────────────────────────────────────────────────────────────┤
│  TRADE HISTORY (most recent first)                                │
│  Time     Strategy  Market               Side  Price  Amount P&L │
│  10:45    sniper    BTC>100k updown       SELL $0.15  $7.50 +$6.5│
│  10:23    mm        ETH>5k Jul25          BUY  $0.48  $12.0  -   │
└───────────────────────────────────────────────────────────────────┘
"""
import asyncio
from datetime import datetime

import config as cfg
from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Label, RichLog, Static


class TrialScreen(Widget):
    """Paper trading performance dashboard with real-time P&L."""

    DEFAULT_CSS = """
    TrialScreen {
        layout: vertical;
        padding: 1;
        height: 1fr;
    }

    .trial-header {
        background: $accent;
        color: $text;
        text-align: center;
        height: 1;
        text-style: bold;
        margin-bottom: 1;
    }

    .stats-bar {
        height: 3;
        background: $panel;
        border: solid $accent;
        padding: 0 2;
        margin-bottom: 1;
        content-align: left middle;
    }

    .section-title {
        background: $primary;
        color: $text;
        text-style: bold;
        height: 1;
        padding: 0 1;
    }

    DataTable {
        height: 1fr;
        margin-bottom: 1;
    }

    .warning {
        color: $warning;
        text-align: center;
        text-style: bold;
    }
    """

    def __init__(self):
        super().__init__()
        self._wallet = None
        self._last_refresh = None

    def compose(self) -> ComposeResult:
        if not cfg.PAPER_TRADE:
            yield Static(
                "Paper trading is OFF.\nSet PAPER_TRADE=true in .env to enable.",
                classes="warning",
            )
            return

        yield Static(" PAPER TRADING MODE — Virtual $50 Account ", classes="trial-header")
        yield Static(id="stats-bar", classes="stats-bar")

        yield Static(" OPEN POSITIONS (P&L from real prices, refreshes every 30s) ", classes="section-title")
        yield DataTable(id="open-table")

        yield Static(" TRADE HISTORY ", classes="section-title")
        yield DataTable(id="history-table")

    async def on_mount(self):
        if not cfg.PAPER_TRADE:
            return

        try:
            from core.paper_trading.wallet import get_paper_wallet
            self._wallet = get_paper_wallet()
        except Exception:
            pass

        # Setup tables
        open_t = self.query_one("#open-table", DataTable)
        open_t.add_columns("Market", "Side", "Shares", "Entry", "Current", "P&L", "P&L%", "Strategy")

        hist_t = self.query_one("#history-table", DataTable)
        hist_t.add_columns("Time", "Strategy", "Market", "Side", "Price", "Shares", "Total")

        self._refresh_display()
        # Auto-refresh every 30s
        self.set_interval(30, self._refresh_display)

    def on_show(self):
        self._refresh_display()

    def _refresh_display(self):
        if not self._wallet:
            return
        self._update_stats()
        self._update_open_positions()
        self._update_history()
        self._last_refresh = datetime.now().strftime("%H:%M:%S")

    def _update_stats(self):
        stats = self._wallet.get_stats()
        balance = stats["balance"]
        total_pnl = stats["total_pnl"]
        ret_pct = stats["total_return_pct"]
        win_rate = stats["win_rate"]
        n_open = stats["open_positions"]
        n_trades = stats["trade_count"]
        unrealized = stats["unrealized_pnl"]
        realized = stats["realized_pnl"]

        bal_color = "green" if balance >= cfg.PAPER_STARTING_BALANCE else "red"
        pnl_color = "green" if total_pnl >= 0 else "red"
        sign = "+" if total_pnl >= 0 else ""

        text = (
            f"[bold]Balance:[/bold] [{bal_color}]${balance:.2f}[/{bal_color}]  |  "
            f"[bold]Total P&L:[/bold] [{pnl_color}]{sign}${total_pnl:.2f} ({ret_pct:+.1f}%)[/{pnl_color}]  |  "
            f"[bold]Realized:[/bold] ${realized:+.2f}  "
            f"[bold]Unrealized:[/bold] ${unrealized:+.2f}  |  "
            f"[bold]Win Rate:[/bold] {win_rate:.0f}%  "
            f"[bold]Trades:[/bold] {n_trades}  "
            f"[bold]Open:[/bold] {n_open}  |  "
            f"[dim]Updated: {self._last_refresh or '--:--:--'}[/dim]"
        )
        try:
            self.query_one("#stats-bar", Static).update(text)
        except Exception:
            pass

    def _update_open_positions(self):
        table = self.query_one("#open-table", DataTable)
        table.clear()
        if not self._wallet.open_positions:
            table.add_row("No open paper positions", "", "", "", "", "", "", "")
            return

        for pos in self._wallet.open_positions:
            pnl_color = "green" if pos.pnl >= 0 else "red"
            pnl_str = f"${pos.pnl:+.2f}"
            pct_str = f"{pos.pnl_pct:+.1f}%"
            current = f"${pos.current_price:.3f}" if pos.current_price > 0 else "..."
            table.add_row(
                pos.market_name[:28],
                pos.outcome,
                f"{pos.shares:.0f}x",
                f"${pos.entry_price:.3f}",
                current,
                pnl_str,
                pct_str,
                pos.strategy,
            )

    def _update_history(self):
        table = self.query_one("#history-table", DataTable)
        table.clear()
        trades = self._wallet.get_recent_trades(limit=30)
        if not trades:
            table.add_row("No paper trades yet — start a strategy!", "", "", "", "", "", "")
            return

        for t in trades:
            ts = t.timestamp[11:19]  # HH:MM:SS only
            table.add_row(
                ts,
                t.strategy,
                t.market_name[:25],
                t.side,
                f"${t.price:.3f}",
                f"{t.shares:.0f}",
                f"${t.total:.2f}",
            )
