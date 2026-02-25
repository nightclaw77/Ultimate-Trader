"""
Orderbook Sniper Strategy - with Paper Trading + Telegram support
Standing low-price buy orders waiting for panic sellers.
"""
import asyncio, logging, math, time, uuid
import config as cfg
from core.notifications import get_notifier
from core.paper_trading.smart_executor import SmartExecutor
from core.risk.portfolio import TradeRecord, get_portfolio
from core.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)
SLOT = 300
BUFFER = 30


class Sniper(BaseStrategy):
    def __init__(self):
        super().__init__("Sniper")
        self._executor = SmartExecutor()
        self._notifier = get_notifier()
        self._seen: set = set()
        self._standing: dict = {}

    async def on_start(self):
        await self._executor.start()
        cost = cfg.SNIPER_PRICE * cfg.SNIPER_SHARES * 2 * len(cfg.SNIPER_ASSETS)
        self.emit_alert("success", f"[{self._executor.mode}] Sniper: {', '.join(cfg.SNIPER_ASSETS)} @${cfg.SNIPER_PRICE} cost=${cost:.2f}/cycle")

    async def on_stop(self):
        await self._executor.stop()
        for oid in list(self._standing.keys()):
            await self._client.cancel_order(oid)

    async def run_once(self):
        await self._place_orders()
        await self._check_fills()
        await asyncio.sleep(30)

    async def _place_orders(self):
        now = int(time.time())
        cur = math.floor(now / SLOT) * SLOT
        into = now - cur
        for asset in cfg.SNIPER_ASSETS:
            if into > BUFFER:
                key = f"{asset}-{cur}"
                if key not in self._seen:
                    await self._snipe(asset, cur, key)
            nxt = cur + SLOT
            nkey = f"{asset}-{nxt}"
            if nkey not in self._seen:
                await self._snipe(asset, nxt, nkey)

    async def _snipe(self, asset: str, slot: int, key: str):
        markets = await self._client.search_markets(f"{asset} updown 5m", limit=10)
        mkt = next((m for m in markets if asset.lower() in m.get("question","").lower()), None)
        if not mkt:
            return
        cid = mkt.get("condition_id") or mkt.get("conditionId", "")
        tokens = mkt.get("tokens") or mkt.get("outcomes", [])
        if not cid or len(tokens) < 2:
            return
        yes_tok = no_tok = None
        for t in tokens:
            out = (t.get("outcome") or t.get("name","")).upper()
            tid = t.get("token_id") or t.get("id","")
            if "YES" in out or "UP" in out: yes_tok = tid
            elif "NO" in out or "DOWN" in out: no_tok = tid
        if not yes_tok or not no_tok:
            return
        self._seen.add(key)
        q = mkt.get("question", f"{asset}")
        self.emit_alert("info", f"[{self._executor.mode}] Sniping: {q[:30]} @${cfg.SNIPER_PRICE}")
        for tok, side in [(yes_tok,"YES"),(no_tok,"NO")]:
            r = await self._executor.place_limit(tok, "BUY", cfg.SNIPER_PRICE, cfg.SNIPER_SHARES, "sniper", cid, q, side)
            if r:
                oid = r.get("order_id", f"sniper_{uuid.uuid4().hex[:8]}")
                self._standing[oid] = {"oid": oid, "tok": tok, "cid": cid, "q": q,
                                        "side": side, "price": cfg.SNIPER_PRICE, "shares": cfg.SNIPER_SHARES}

    async def _check_fills(self):
        if not self._standing:
            return
        try:
            open_ids = {(o.get("id") or o.get("order_id")) for o in await self._client.get_open_orders()}
            for oid, info in list(self._standing.items()):
                if oid not in open_ids:
                    await self._handle_fill(oid, info)
                    del self._standing[oid]
        except Exception as e:
            logger.warning(f"Fill check: {e}")

    async def _handle_fill(self, oid: str, info: dict):
        self.emit_alert("success", f"SNIPER FILL! {info['q'][:25]} {info['side']} @${info['price']:.2f} x{info['shares']}")
        self._notifier.sniper_fill(info["q"], info["price"], info["shares"], paper=cfg.PAPER_TRADE)
        await self._executor.place_limit(
            info["tok"], "SELL", cfg.SNIPER_SELL_TARGET, info["shares"], "sniper",
            info["cid"], info["q"], info["side"],
        )
        self._portfolio.record_trade(TradeRecord(
            trade_id=oid, strategy="sniper", market_name=info["q"],
            condition_id=info["cid"], token_id=info["tok"], side="BUY",
            price=info["price"], size=info["shares"], total=info["price"]*info["shares"], status="FILLED",
        ))

    def get_status(self):
        cost = cfg.SNIPER_PRICE * cfg.SNIPER_SHARES * 2 * len(cfg.SNIPER_ASSETS)
        return {**super().get_status(), "standing": len(self._standing), "mode": self._executor.mode, "cost_per_cycle": cost}
