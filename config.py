"""
Ultimate-Trader Configuration
Cascade: env vars > .env file > defaults
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent / ".env")


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _get_bool(key: str, default: bool = False) -> bool:
    val = _get(key, str(default)).lower()
    return val in ("true", "1", "yes")


def _get_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_get(key, str(default)))
    except ValueError:
        return default


def _get_int(key: str, default: int = 0) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


def _get_list(key: str, default: str = "") -> list[str]:
    val = _get(key, default)
    return [x.strip() for x in val.split(",") if x.strip()]


# ---- API Endpoints (from polymarket-terminal analysis) ----
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
DATA_HOST = "https://data-api.polymarket.com"
WS_HOST = "wss://ws-live-data.polymarket.com"
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# ---- Polymarket Auth ----
PRIVATE_KEY = _get("POLYMARKET_PRIVATE_KEY")
FUNDER_ADDRESS = _get("POLYMARKET_FUNDER_ADDRESS")
API_KEY = _get("POLYMARKET_API_KEY")
API_SECRET = _get("POLYMARKET_SECRET")
API_PASSPHRASE = _get("POLYMARKET_PASSPHRASE")

# ---- AI ----
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")

# ---- Telegram ----
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")

# ---- Safety ----
DRY_RUN = _get_bool("DRY_RUN", default=True)
MAX_POSITION_USDC = _get_float("MAX_POSITION_USDC", default=50.0)
DAILY_LOSS_LIMIT = _get_float("DAILY_LOSS_LIMIT", default=20.0)
MAX_OPEN_POSITIONS = _get_int("MAX_OPEN_POSITIONS", default=5)

# ---- Copy Trader ----
COPY_TRADER_ADDRESS = _get("COPY_TRADER_ADDRESS")
COPY_SIZE_PERCENT = _get_float("COPY_SIZE_PERCENT", default=10.0)
COPY_AUTO_SELL_PROFIT = _get_float("COPY_AUTO_SELL_PROFIT", default=20.0)

# ---- Market Maker ----
MM_ASSETS = _get_list("MM_ASSETS", "BTC,ETH")
MM_TRADE_SIZE = _get_float("MM_TRADE_SIZE", default=10.0)
MM_SELL_PRICE = _get_float("MM_SELL_PRICE", default=0.60)
MM_CUT_LOSS_TIME = _get_int("MM_CUT_LOSS_TIME", default=120)
MM_RECOVERY_BUY = _get_bool("MM_RECOVERY_BUY", default=False)

# ---- Sniper ----
SNIPER_ASSETS = _get_list("SNIPER_ASSETS", "BTC,ETH,SOL")
SNIPER_PRICE = _get_float("SNIPER_PRICE", default=0.02)
SNIPER_SHARES = _get_int("SNIPER_SHARES", default=50)
SNIPER_SELL_TARGET = _get_float("SNIPER_SELL_TARGET", default=0.15)

# ---- Data paths ----
DATA_DIR = Path(__file__).parent / "data"
LOGS_DIR = Path(__file__).parent / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

POSITIONS_FILE = DATA_DIR / "positions.json"
TRADES_FILE = DATA_DIR / "trades.json"
WATCHED_WALLETS_FILE = DATA_DIR / "watched_wallets.json"
SIM_STATS_FILE = DATA_DIR / "sim_stats.json"


def validate() -> list[str]:
    """Returns list of missing critical config."""
    errors = []
    if not PRIVATE_KEY:
        errors.append("POLYMARKET_PRIVATE_KEY is required")
    if not FUNDER_ADDRESS:
        errors.append("POLYMARKET_FUNDER_ADDRESS is required")
    if not API_KEY:
        errors.append("POLYMARKET_API_KEY is required")
    return errors


def summary() -> str:
    """Returns a human-readable config summary (no secrets)."""
    lines = [
        f"DRY_RUN: {'YES (safe mode)' if DRY_RUN else 'NO (live trading!)'}",
        f"Max Position: ${MAX_POSITION_USDC} USDC",
        f"Daily Loss Limit: ${DAILY_LOSS_LIMIT}",
        f"Max Open Positions: {MAX_OPEN_POSITIONS}",
        f"MM Assets: {', '.join(MM_ASSETS)}",
        f"Sniper Assets: {', '.join(SNIPER_ASSETS)}",
        f"Copy Trader: {'configured' if COPY_TRADER_ADDRESS else 'not set'}",
    ]
    return "\n".join(lines)
