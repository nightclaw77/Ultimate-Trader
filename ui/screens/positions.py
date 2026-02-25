"""
Positions Screen - Active positions and trade history.
"""
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Label, Static, TabbedContent, TabPane

from core.risk.portfolio import get_portfolio


class PositionsScreen(Widget):
    """Shows active positions and complete trade history."""

    DEFAULT_CSS = """
    PositionsScreen {
        layout: vertical;
        padding: 1;
        height: 1fr;
    }

    DataTable {
        height: 1fr;
    }

    .summary {
        height: 3;
        background: $panel;
        padding: 0 2;
        border: solid $primary;
    }
    """

    def __init__(self):
        super().__init__()
        self._portfolio = get_portfolio()

    def compose(self) -> ComposeResult:
        yield Static(id="portfolio-summary", classes="summary")
        with TabbedContent():
            with TabPane("Active Positions"):
                yield DataTable(id="active-table")
            with TabPane("Trade History"):
                yield DataTable(id="history-table")

    async def on_mount(self):
        # Active positions table
        active = self.query_one("#active-table", DataTable)
        active.add_columns("Market", "Outcome", "Shares", "Avg Price", "Cost", "P&L", "Strategy", "Status")

        # History table
        hist = self.query_one("#history-table", DataTable)
        hist.add_columns("Time", "Strategy", "Market", "Side", "Price", "Size", "Total", "Status")

        self._refresh()

    def on_show(self):
        self._refresh()

    def _refresh(self):
        self._update_summary()
        self._update_active()
        self._update_history()

    def _update_summary(self):
        stats = self._portfolio.get_stats()
        daily = stats["daily_pnl"]
        total = stats["total_pnl"]
        dc = "green" if daily >= 0 else "red"
        tc = "green" if total >= 0 else "red"
        text = (
            f"Open: {stats['open_positions']}  |  "
            f"Invested: ${stats['total_invested']:.2f}  |  "
            f"Daily: [{dc}]${daily:+.2f}[/{dc}]  |  "
            f"Total: [{tc}]${total:+.2f}[/{tc}]  |  "
            f"Win Rate: {stats['win_rate']:.0f}%  |  "
            f"Trades: {stats['total_trades']}"
        )
        try:
            self.query_one("#portfolio-summary", Static).update(text)
        except Exception:
            pass

    def _update_active(self):
        table = self.query_one("#active-table", DataTable)
        table.clear()
        positions = self._portfolio.get_open_positions()

        if not positions:
            table.add_row("No open positions", "", "", "", "", "", "", "")
            return

        for pos in positions:
            pnl_str = f"${pos.pnl:+.2f}"
            table.add_row(
                pos.market_name[:25],
                pos.outcome,
                f"{pos.shares:.1f}",
                f"${pos.avg_buy_price:.3f}",
                f"${pos.total_cost:.2f}",
                pnl_str,
                pos.strategy,
                pos.status,
            )

    def _update_history(self):
        table = self.query_one("#history-table", DataTable)
        table.clear()
        trades = self._portfolio.get_recent_trades(limit=100)

        if not trades:
            table.add_row("No trades yet", "", "", "", "", "", "", "")
            return

        for t in reversed(trades):
            ts = t.timestamp[:19].replace("T", " ")
            table.add_row(
                ts,
                t.strategy,
                t.market_name[:20],
                t.side,
                f"${t.price:.3f}",
                f"{t.size:.1f}",
                f"${t.total:.2f}",
                t.status,
            )
