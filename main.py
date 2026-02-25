#!/usr/bin/env python3
"""
Ultimate Trader - Terminal Trading Platform for Polymarket
Entry point: python3 main.py

Synthesized from:
- polymarket-terminal (Copy Trade, Market Making, Orderbook Sniper)
- nautilus_trader (data structures, event patterns, retry logic)
- polymarket-cli (API patterns, config system)
- Scrapling (stealth news scraping)
- Claude AI (market analysis)
"""
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

import config as cfg

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(cfg.LOGS_DIR / "trading.log"),
        # Don't log to stderr - TUI will handle display
    ],
)
logger = logging.getLogger(__name__)


def check_environment():
    """Validate environment before starting."""
    errors = cfg.validate()
    if errors:
        print("\n❌ Configuration errors:")
        for e in errors:
            print(f"   • {e}")
        print("\nPlease edit .env file and set the required values.")
        print("See .env.example for reference.\n")
        return False

    print(f"\n{'='*50}")
    print("  Ultimate Trader")
    print(f"{'='*50}")
    print(f"\nMode: {'DRY RUN (safe)' if cfg.DRY_RUN else '⚠  LIVE TRADING'}")
    print(f"Funder: {cfg.FUNDER_ADDRESS[:10]}...{cfg.FUNDER_ADDRESS[-6:]}")
    print(f"Max Position: ${cfg.MAX_POSITION_USDC} USDC")
    print(f"Daily Limit: ${cfg.DAILY_LOSS_LIMIT} USDC")
    print("\nStarting TUI...\n")
    return True


def main():
    """Main entry point."""
    if not check_environment():
        sys.exit(1)

    from ui.app import UltimateTraderApp
    app = UltimateTraderApp()
    app.run()


if __name__ == "__main__":
    main()
