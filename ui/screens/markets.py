"""
Markets Screen - Browse and search Polymarket prediction markets.
"""
import json

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
            markets = await self._client.get_markets(limit=50, order="volume")

        self._all_markets = markets

        for m in markets:
            question = m.get("question") or m.get("title", "N/A")
            # outcomePrices is a JSON string: '["0.615", "0.385"]'
            raw_prices = m.get("outcomePrices") or "[]"
            try:
                prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
                prob = f"{float(prices[0])*100:.1f}%" if prices else "?"
            except Exception:
                ltp = m.get("lastTradePrice")
                prob = f"{float(ltp)*100:.1f}%" if ltp else "?"

            volume = float(m.get("volumeNum") or m.get("volume") or 0)
            liq = float(m.get("liquidityNum") or m.get("liquidity") or 0)

            def fmt_usd(v):
                if v >= 1_000_000:
                    return f"${v/1_000_000:.1f}M"
                if v >= 1_000:
                    return f"${v/1_000:.0f}K"
                return f"${v:.0f}"

            end = (m.get("endDateIso") or m.get("end_date_iso", ""))[:10]

            table.add_row(
                question[:45],
                prob,
                fmt_usd(volume),
                fmt_usd(liq),
                end,
            )

    async def on_input_submitted(self, event: Input.Submitted):
        await self._load_markets(event.value)
