"""
Telegram Notification System
Sends trade alerts, P&L updates, and system events to Telegram.
"""
import asyncio
import logging
from typing import Optional

import aiohttp

import config as cfg

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"


class TelegramNotifier:
    """
    Sends notifications to Telegram channel/chat.
    Non-blocking: notifications are queued and sent in background.
    """

    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._task: Optional[asyncio.Task] = None
        self._ready = bool(cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID)

        if self._ready:
            logger.info("Telegram notifier configured")
        else:
            logger.info("Telegram not configured (TELEGRAM_BOT_TOKEN/CHAT_ID missing)")

    async def start(self):
        if not self._ready:
            return
        self._session = aiohttp.ClientSession()
        self._task = asyncio.create_task(self._send_loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _send_loop(self):
        """Background loop that sends queued messages."""
        while True:
            try:
                message = await self._queue.get()
                await self._send(message)
                await asyncio.sleep(0.5)  # Rate limiting
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Telegram send error: {e}")

    async def _send(self, text: str):
        """Send message to Telegram."""
        if not self._session or not self._ready:
            return
        try:
            url = f"{TELEGRAM_API}/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": cfg.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    logger.warning(f"Telegram error {resp.status}")
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    def _queue_message(self, text: str):
        """Non-blocking queue a message for sending."""
        if not self._ready:
            return
        try:
            self._queue.put_nowait(text)
        except asyncio.QueueFull:
            pass

    # ---- Notification methods ----

    def trade_opened(
        self,
        strategy: str,
        market: str,
        side: str,
        price: float,
        amount: float,
        paper: bool = True,
    ):
        mode = "📄 PAPER" if paper else "💵 LIVE"
        emoji = "🟢" if side == "BUY" else "🔴"
        self._queue_message(
            f"{mode} | {emoji} <b>{strategy.upper()}</b>\n"
            f"<b>{side}</b> {market[:40]}\n"
            f"Price: ${price:.3f} | Amount: ${amount:.2f}"
        )

    def trade_closed(
        self,
        strategy: str,
        market: str,
        pnl: float,
        paper: bool = True,
    ):
        mode = "📄 PAPER" if paper else "💵 LIVE"
        sign = "+" if pnl >= 0 else ""
        emoji = "✅" if pnl >= 0 else "❌"
        self._queue_message(
            f"{mode} | {emoji} <b>{strategy.upper()}</b> CLOSED\n"
            f"{market[:40]}\n"
            f"P&L: <b>{sign}${pnl:.2f}</b>"
        )

    def sniper_fill(self, market: str, price: float, shares: float, paper: bool = True):
        mode = "📄 PAPER" if paper else "💵 LIVE"
        potential = shares * 1.0  # Max payout if wins
        self._queue_message(
            f"{mode} | 🎯 <b>SNIPER FILL!</b>\n"
            f"{market[:40]}\n"
            f"Filled @${price:.3f} x{shares:.0f}\n"
            f"Max payout if wins: ${potential:.2f}"
        )

    def daily_summary(self, balance: float, pnl: float, trades: int, win_rate: float, paper: bool = True):
        mode = "📄 PAPER" if paper else "💵 LIVE"
        sign = "+" if pnl >= 0 else ""
        self._queue_message(
            f"{mode} | 📊 <b>Daily Summary</b>\n"
            f"Balance: ${balance:.2f}\n"
            f"P&L: <b>{sign}${pnl:.2f}</b>\n"
            f"Trades: {trades} | Win Rate: {win_rate:.0f}%"
        )

    def system_alert(self, message: str, level: str = "info"):
        emoji = {"info": "ℹ️", "warning": "⚠️", "error": "🚨"}.get(level, "ℹ️")
        self._queue_message(f"{emoji} <b>Ultimate Trader</b>\n{message}")


# Singleton
_notifier: Optional[TelegramNotifier] = None


def get_notifier() -> TelegramNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramNotifier()
    return _notifier
