#!/usr/bin/env python3
"""
Execution Bridge: TradingAgents decision → Alpaca paper-trading order.

Usage:
  # Run agent + auto-execute (paper trading)
  .venv/bin/python execute_order.py NVDA 2026-09-18

  # Dry run: show what order WOULD be placed, without submitting
  .venv/bin/python execute_order.py NVDA 2026-09-18 --dry-run

  # Execute from a previously-saved state log (no re-run)
  .venv/bin/python execute_order.py NVDA 2026-09-18 --from-log
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Shared execution logic implemented in webapp.execution
from webapp.execution import (
    build_order,
    execute,
    fetch_last_close_price,
    get_account_overview,
    get_alpaca_credentials,
    get_alpaca_portfolio_context,
    get_alpaca_trading_client,
    main,
    parse_position_sizing,
    parse_trader_decision,
    submit_alpaca_order,
)

__all__ = [
    "parse_position_sizing",
    "parse_trader_decision",
    "build_order",
    "execute",
    "main",
    "get_alpaca_credentials",
    "get_alpaca_trading_client",
    "get_account_overview",
    "get_alpaca_portfolio_context",
    "fetch_last_close_price",
    "submit_alpaca_order",
]

if __name__ == "__main__":
    main()
