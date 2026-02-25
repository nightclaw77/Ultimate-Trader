"""
Markets Screen - Browse and search Polymarket prediction markets.
"""
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Input, Label, Static

from core.polymarket.client import get_client


class MarketsScreen(Widget):
    """Browse all Polymarket prediction markets with search."""

    DEFAULT_CSS = """
    MarketsScreen {
        layout: vertical;
        padding: 1;
        height: 1fr;
    }

    Input {
        margin-bottom: 1;
    }

    DataTable {
        height: 1fr;
    }
    """

    def __init__(self):
        super().__init__()
        self._client = get_client()
        self._all_markets: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Static("PREDICTION MARKET BROWSER", classes="panel-title")
        yield Input(placeholder="Search markets... (press Enter)", id="search-input")
        yield DataTable(id="markets-table")

    async def on_mount(self):
        table = self.query_one("#markets-table", DataTable)
        table.add_columns("Market", "Prob YES", "Volume", "Liquidity", "End Date")
        await self._load_markets()

    async def _load_markets(self, query: str = ""):
        table = self.query_one("#markets-table", DataTable)
        table.clear()

        if query:
            markets = await self._client.search_markets(query, limit=50)
        else:
            markets = await self._client.get_markets(limit=50, order="volume_num")

        self._all_markets = markets

        for m in markets:
            question = m.get("question") or m.get("title", "N/A")
            tokens = m.get("tokens") or []
            yes_price = 0
            if tokens:
                for t in tokens:
                    outcome = (t.get("outcome") or "").upper()
                    if "YES" in outcome:
                        yes_price = float(t.get("price", 0))
                        break

            prob = f"{yes_price*100:.1f}%" if yes_price else "?"
            volume = m.get("volume") or m.get("volume_num", 0)
            liq = m.get("liquidity") or m.get("liquidity_num", 0)

            def fmt_usd(v):
                v = float(v or 0)
                if v >= 1_000_000:
                    return f"${v/1_000_000:.1f}M"
                if v >= 1_000:
                    return f"${v/1_000:.0f}K"
                return f"${v:.0f}"

            end = (m.get("end_date_iso") or m.get("endDateIso", ""))[:10]

            table.add_row(
                question[:45],
                prob,
                fmt_usd(volume),
                fmt_usd(liq),
                end,
            )

    async def on_input_submitted(self, event: Input.Submitted):
        await self._load_markets(event.value)
