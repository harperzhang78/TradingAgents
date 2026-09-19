"""Configuration and environment management for the TradingAgents Web App."""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Root of the TradingAgents project
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load environment variables from .env at project root
ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# Local data directory for SQLite and state persistence
WEBAPP_DIR = PROJECT_ROOT / "webapp"
DATA_DIR = WEBAPP_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = DATA_DIR / "trading_dashboard.db"

# Alpaca configuration
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_PAPER = os.environ.get("ALPACA_PAPER", "true").lower() != "false"

# Server configuration
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8037

# Default settings stored in DB on first launch
DEFAULT_AUTO_TRADE = False
DEFAULT_SCHEDULE_INTERVAL_MINUTES = 1440  # 24 hours / Daily
DEFAULT_SCHEDULE_ENABLED = False
DEFAULT_WATCHLIST = ["NVDA", "AAPL", "MSFT"]
