"""
Market Maker Strategy - with Paper Trading + Telegram support
5-minute slot liquidity provision on Polymarket.
"""
import asyncio, logging, math, time, uuid
import config as cfg
from core.notifications import get_notifier
from core.paper_trading.smart_executor import SmartExecutor
from core.risk.manager import RiskError
from core.risk.portfolio import MarketPosition, TradeRecord, get_portfolio
from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)
SLOT = 300


def next_slot():
    now = int(time.time())
    return (math.floor(now / SLOT) * SLOT) + SLOT

def time_to_slot_end():
    now = int(time.time())
    return SLOT - (now % SLOT)


class MarketMaker(BaseStrategy):
    def __init__(self):
        super().__init__("MarketMaker")
        self._executor = SmartExecutor()
        self._notifier = get_notifier()
        self._active: dict = {}
        self._seen: set = set()

    async def on_start(self):
        await self._executor.start()
        self.emit_alert("success", f"[{self._executor.mode}] MM: {', '.join(cfg.MM_ASSETS)}")

    async def on_stop(self):
        await self._executor.stop()

    async def run_once(self):
        await self._detect_and_enter()
        await self._monitor()
        await asyncio.sleep(10)

    async def _detect_and_enter(self):
        ns = next_slot()
        for asset in cfg.MM_ASSETS:
            key = f"{asset}-{ns}"
            if key in self._seen:
                continue
            markets = await self._client.search_markets(f"{asset} updown 5m", limit=10)
            for m in markets:
                q = m.get("question", "").lower()
                if asset.lower() not in q:
                    continue
                cid = m.get("condition_id") or m.get("conditionId", "")
                if not cid or cid in self._active:
                    continue
                try:
                    self._risk.check_new_position(cfg.MM_TRADE_SIZE, q)
                except RiskError as e:
                    self.emit_alert("warning", f"MM risk: {e}")
                    continue
                self._seen.add(key)
                await self._enter(m, asset)
                break

    async def _enter(self, market: dict, asset: str):
        cid = market.get("condition_id") or market.get("conditionId", "")
        q = market.get("question", f"{asset} 5m")
        tokens = market.get("tokens") or market.get("outcomes", [])
        yes_tok = no_tok = None
        for t in tokens:
            out = (t.get("outcome") or t.get("name", "")).upper()
            tid = t.get("token_id") or t.get("id", "")
            if "YES" in out or "UP" in out:
                yes_tok = tid
            elif "NO" in out or "DOWN" in out:
                no_tok = tid
        if not yes_tok or not no_tok:
            return
        shares = cfg.MM_TRADE_SIZE / 0.50
        self.emit_alert("info", f"[{self._executor.mode}] MM entering: {q[:30]} ${cfg.MM_TRADE_SIZE}")
        yes_r = await self._executor.place_limit(yes_tok, "SELL", cfg.MM_SELL_PRICE, shares, "mm", cid, q, "YES")
        no_r  = await self._executor.place_limit(no_tok,  "SELL", cfg.MM_SELL_PRICE, shares, "mm", cid, q, "NO")
        if yes_r and no_r:
            self._active[cid] = {"cid": cid, "q": q, "yes_tok": yes_tok, "no_tok": no_tok,
                                   "yes_ord": (yes_r or {}).get("order_id", ""),
                                   "no_ord": (no_r or {}).get("order_id", ""),
                                   "entered": int(time.time()), "size": cfg.MM_TRADE_SIZE}
            self._notifier.trade_opened("mm", q, "BUY/SELL", 0.50, cfg.MM_TRADE_SIZE, paper=cfg.PAPER_TRADE)
            self.emit_alert("success", f"MM orders: {q[:25]} sell@{cfg.MM_SELL_PRICE}")

    async def _monitor(self):
        to_remove = []
        for cid, state in self._active.items():
            if time_to_slot_end() <= cfg.MM_CUT_LOSS_TIME:
                self.emit_alert("warning", f"MM cut-loss: {state['q'][:25]}")
                await self._client.cancel_order(state.get("yes_ord", ""))
                await self._client.cancel_order(state.get("no_ord", ""))
                to_remove.append(cid)
        for c in to_remove:
            del self._active[c]

    def get_status(self):
        return {**super().get_status(), "active": len(self._active), "mode": self._executor.mode}
