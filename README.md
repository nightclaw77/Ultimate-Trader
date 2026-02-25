# Ultimate Trader

A professional terminal trading platform for [Polymarket](https://polymarket.com) prediction markets.

Built as a TUI (Terminal User Interface) — runs directly in your terminal via SSH, no browser required.

## Features

### 3 Trading Strategies

| Strategy | How it works | Risk |
|----------|-------------|------|
| **Copy Trader** | Monitors a profitable wallet and mirrors their trades | Low |
| **Market Maker** | Provides liquidity on 5-minute binary markets, captures spread | Medium |
| **Orderbook Sniper** | Standing low-price orders waiting for panic sellers | Low-cost, sporadic profit |

### Platform Features
- Beautiful TUI dashboard with live market data
- Real-time alerts and trade notifications
- Portfolio tracker with P&L calculation
- AI market analysis (Claude API)
- News sentiment analysis (Scrapling)
- Safe by default: `DRY_RUN=true`

## Quick Start

```bash
# Clone
git clone https://github.com/nightclaw77/Ultimate-Trader
cd Ultimate-Trader

# Install dependencies
pip install -r requirements.txt --break-system-packages

# Configure
cp .env.example .env
nano .env  # Add your Polymarket credentials

# Run (safe mode - no real trades)
python3 main.py

# When ready for real trading:
# Set DRY_RUN=false in .env
```

## Navigation

| Key | Action |
|-----|--------|
| `1` | Dashboard |
| `2` | Market Browser |
| `3` | Positions & History |
| `4` | Bot Control |
| `Q` | Quit |

## Configuration

See `.env.example` for all configuration options.

**Critical safety settings:**
- `DRY_RUN=true` — Default. No real trades until you change this.
- `MAX_POSITION_USDC=50` — Max $50 per position
- `DAILY_LOSS_LIMIT=20` — Stop if daily loss > $20

## Run in Background (VPS)

```bash
# Using nohup
nohup python3 main.py > logs/trading.log 2>&1 &

# Check logs
tail -f logs/trading.log

# Stop
kill $(cat .pid)
```

## Architecture

Built by synthesizing the best patterns from:
- [polymarket-terminal](https://github.com/direkturcrypto/polymarket-terminal) — Strategy logic
- [nautilus_trader](https://github.com/nautechsystems/nautilus_trader) — Data structures & patterns
- [polymarket-cli](https://github.com/Polymarket/polymarket-cli) — API patterns

## Disclaimer

Trading prediction markets involves financial risk. This software is provided as-is with no guarantees. Always test with `DRY_RUN=true` first. Start with small position sizes. You are responsible for all trading losses.
