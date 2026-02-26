"""
Bot Control Screen - Start/stop strategies and view config.
"""
from typing import Callable

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Button, DataTable, Label, Static, Switch

import config as cfg


class BotControlScreen(Widget):
    """Control panel for all trading strategies."""

    DEFAULT_CSS = """
    BotControlScreen {
        layout: vertical;
        padding: 1;
        height: 1fr;
        overflow-y: auto;
    }

    .strategy-card {
        border: solid $primary;
        padding: 1;
        margin-bottom: 1;
        layout: horizontal;
        height: 5;
    }

    .strategy-info {
        width: 2fr;
    }

    .strategy-controls {
        width: 1fr;
        align: right middle;
    }

    .config-panel {
        border: solid $accent;
        padding: 1;
        margin-top: 1;
    }

    .warning-text {
        color: $warning;
        text-style: bold;
    }
    """

    def __init__(self, strategies: dict, toggle_callback: Callable):
        super().__init__()
        self._strategies = strategies
        self._toggle = toggle_callback

    def compose(self) -> ComposeResult:
        mode = cfg.trading_mode()
        if mode == "PAPER":
            yield Static("PAPER TRADE MODE — Virtual money only, real market prices", classes="warning-text")
        elif mode == "DRY_RUN":
            yield Static("DRY RUN MODE — Log only, no execution", classes="warning-text")
        else:
            yield Static("LIVE TRADING MODE — Real funds at risk!", classes="warning-text")

        yield Static("")

        # Auto Trader (NEW — main strategy)
        with Widget(classes="strategy-card"):
            with Widget(classes="strategy-info"):
                yield Label("[bold cyan]Auto Trader[/bold cyan]  [dim](Crypto Up/Down — 5-15 min markets)[/dim]")
                yield Label(
                    f"BTC+ETH+SOL+XRP  |  "
                    f"Size: ${cfg.BASE_TRADE_SIZE:.0f}-${cfg.MAX_TRADE_SIZE:.0f}  |  "
                    f"Profit: +{int(cfg.AUTO_PROFIT_TARGET*100)}%  "
                    f"Stop: -{int(cfg.AUTO_STOP_LOSS*100)}%  |  "
                    f"Max {cfg.MAX_OPEN_AUTO_TRADES} trades"
                )
            with Widget(classes="strategy-controls"):
                yield Button("START", id="start-auto", variant="success")
                yield Button("Stop", id="stop-auto", variant="error")

        # Copy Trader
        with Widget(classes="strategy-card"):
            with Widget(classes="strategy-info"):
                yield Label("[bold]Copy Trader[/bold]")
                target = cfg.COPY_TRADER_ADDRESS[:16] + "..." if cfg.COPY_TRADER_ADDRESS else "Not configured"
                yield Label(f"Target: {target} | Size: {cfg.COPY_SIZE_PERCENT}% | Profit: {cfg.COPY_AUTO_SELL_PROFIT}%")
            with Widget(classes="strategy-controls"):
                yield Button("Start", id="start-copy", variant="success")
                yield Button("Stop", id="stop-copy", variant="error")

        # Market Maker
        with Widget(classes="strategy-card"):
            with Widget(classes="strategy-info"):
                yield Label("[bold]Market Maker[/bold]")
                yield Label(f"Assets: {', '.join(cfg.MM_ASSETS)} | Size: ${cfg.MM_TRADE_SIZE} | Sell: {cfg.MM_SELL_PRICE}")
            with Widget(classes="strategy-controls"):
                yield Button("Start", id="start-mm", variant="success")
                yield Button("Stop", id="stop-mm", variant="error")

        # Sniper
        with Widget(classes="strategy-card"):
            with Widget(classes="strategy-info"):
                yield Label("[bold]Orderbook Sniper[/bold]")
                total_cost = cfg.SNIPER_PRICE * cfg.SNIPER_SHARES * 2 * len(cfg.SNIPER_ASSETS)
                yield Label(f"Assets: {', '.join(cfg.SNIPER_ASSETS)} | Price: ${cfg.SNIPER_PRICE} | Cost: ${total_cost:.2f}/cycle")
            with Widget(classes="strategy-controls"):
                yield Button("Start", id="start-sniper", variant="success")
                yield Button("Stop", id="stop-sniper", variant="error")

        # Config overview
        with Widget(classes="config-panel"):
            yield Static("[bold]Configuration[/bold]")
            yield Static(cfg.summary())

    async def on_button_pressed(self, event: Button.Pressed):
        btn_id = event.button.id
        if btn_id == "start-auto":
            await self._toggle("auto", True)
        elif btn_id == "stop-auto":
            await self._toggle("auto", False)
        elif btn_id == "start-copy":
            await self._toggle("copy", True)
        elif btn_id == "stop-copy":
            await self._toggle("copy", False)
        elif btn_id == "start-mm":
            await self._toggle("mm", True)
        elif btn_id == "stop-mm":
            await self._toggle("mm", False)
        elif btn_id == "start-sniper":
            await self._toggle("sniper", True)
        elif btn_id == "stop-sniper":
            await self._toggle("sniper", False)
