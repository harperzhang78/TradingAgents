"""Execution Bridge: TradingAgents decision → Alpaca paper/live order.

Shared execution module reused by both the web app and the CLI (execute_order.py).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from webapp.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Alpaca Client Helpers
# ---------------------------------------------------------------------------

def get_alpaca_credentials() -> tuple[str, str, bool]:
    """Return (api_key, secret_key, paper_flag)."""
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    paper = os.environ.get("ALPACA_PAPER", "true").lower() != "false"
    return api_key, secret_key, paper


def get_alpaca_trading_client(paper: bool | None = None) -> TradingClient:
    """Instantiate Alpaca TradingClient using configured credentials."""
    api_key, secret_key, default_paper = get_alpaca_credentials()
    if not api_key or not secret_key:
        raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY must be set in environment")
    use_paper = default_paper if paper is None else paper
    return TradingClient(api_key, secret_key, paper=use_paper)


def get_account_overview(client: TradingClient | None = None) -> dict[str, Any]:
    """Fetch current Alpaca account summary and open positions."""
    if client is None:
        client = get_alpaca_trading_client()

    acct = client.get_account()
    positions_raw = client.get_all_positions()

    positions = []
    for p in positions_raw:
        positions.append({
            "symbol": p.symbol,
            "qty": float(str(p.qty)),
            "side": str(p.side),
            "avg_entry_price": float(str(p.avg_entry_price)) if p.avg_entry_price is not None else None,
            "current_price": float(str(p.current_price)) if getattr(p, "current_price", None) is not None else None,
            "market_value": float(str(p.market_value)) if getattr(p, "market_value", None) is not None else None,
            "unrealized_pl": float(str(p.unrealized_pl)) if getattr(p, "unrealized_pl", None) is not None else None,
            "unrealized_plpc": float(str(p.unrealized_plpc)) if getattr(p, "unrealized_plpc", None) is not None else None,
        })

    _, _, paper = get_alpaca_credentials()

    return {
        "status": str(acct.status),
        "equity": float(str(acct.equity)),
        "cash": float(str(acct.cash)),
        "buying_power": float(str(acct.buying_power)),
        "currency": getattr(acct, "currency", "USD"),
        "paper": paper,
        "positions": positions,
    }


def get_alpaca_portfolio_context(client: TradingClient | None = None):
    """Construct a TradingAgents PortfolioContext object from Alpaca holdings."""
    from tradingagents.portfolio import PortfolioContext, Position

    if client is None:
        try:
            client = get_alpaca_trading_client()
        except Exception as e:
            logger.warning("Could not initialize Alpaca client for portfolio context: %s", e)
            return None

    try:
        acct = client.get_account()
        positions_raw = client.get_all_positions()
        pos_list = []
        for p in positions_raw:
            pos_list.append(Position(
                ticker=p.symbol,
                quantity=float(str(p.qty)),
                average_price=float(str(p.avg_entry_price)) if p.avg_entry_price is not None else None,
            ))
        return PortfolioContext(
            cash=float(str(acct.cash)),
            currency=getattr(acct, "currency", "USD"),
            positions=pos_list,
        )
    except Exception as e:
        logger.warning("Failed to fetch Alpaca portfolio context: %s", e)
        return None


def fetch_last_close_price(ticker: str) -> float | None:
    """Fetch last close price from Alpaca data API for reference/sizing (best effort)."""
    api_key, secret_key, _ = get_alpaca_credentials()
    if not api_key or not secret_key:
        return None
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import Timeframe
        import alpaca.data
        hist_client = alpaca.data.StockBarsClient(api_key, secret_key)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=Timeframe.Day, limit=1)
        resp = hist_client.get_stock_bars(req)
        if resp and resp[0] and resp[0].data:
            return float(resp[0].data[-1]["close"])
    except Exception as e:
        logger.debug("Skipped price lookup for %s: %s", ticker, e)
    return None


# ---------------------------------------------------------------------------
# Sizing helper
# ---------------------------------------------------------------------------

def parse_position_sizing(sizing_text: str | None, account_equity: float) -> float | None:
    """Convert a natural-language sizing hint to a dollar notional.
    Default (None / 'not provided') → 5% of account equity.
    """
    if not sizing_text or sizing_text.strip() in ("", "not provided", "None", "N/A"):
        return account_equity * 0.05
    text = sizing_text.strip()
    m = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*(?:of\s+)?portfolio", text, re.I)
    if m:
        return account_equity * float(m.group(1)) / 100.0
    m = re.search(r"\$\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)", text)
    if m:
        return float(m.group(1).replace(",", ""))
    return account_equity * 0.05


# ---------------------------------------------------------------------------
# Decision parser
# ---------------------------------------------------------------------------

def parse_trader_decision(final_state: dict) -> dict[str, Any]:
    """Extract structured trading decision from the graph's final state or a saved log."""
    trader_text = (
        final_state.get("trader_investment_plan")
        or final_state.get("trader_investment_decision")
        or ""
    )
    pm_decision = final_state.get("final_trade_decision", "")

    decision: dict[str, Any] = {
        "action": None,
        "entry_price": None,
        "stop_loss": None,
        "position_sizing": None,
        "reasoning": None,
        "rating": None,
        "price_target": None,
    }

    m = re.search(r"\*\*Action\*\*:\s*(\w+)", trader_text)
    if m:
        decision["action"] = m.group(1).strip()

    m = re.search(r"\*\*Entry Price\*\*:\s*(?:\$\s*)?([\d,]+\.?\d*)", trader_text)
    if m:
        decision["entry_price"] = float(m.group(1).replace(",", ""))

    m = re.search(r"\*\*Stop Loss\*\*:\s*(?:\$\s*)?([\d,]+\.?\d*)", trader_text)
    if m:
        decision["stop_loss"] = float(m.group(1).replace(",", ""))

    m = re.search(r"\*\*Position Sizing\*\*:\s*(.+)", trader_text)
    if m:
        decision["position_sizing"] = m.group(1).strip()

    m = re.search(r"\*\*Reasoning\*\*:\s*(.+?)(?=\n\*\*|\nFINAL|\Z)", trader_text, re.S)
    if m:
        decision["reasoning"] = m.group(1).strip()

    m = re.search(r"\*\*Rating\*\*:\s*(\w+)", pm_decision)
    if m:
        decision["rating"] = m.group(1).strip()

    m = re.search(r"\*\*Price Target\*\*:\s*\$?\s*(\d[\d,]*\.?\d*)", pm_decision)
    if m:
        decision["price_target"] = float(m.group(1).replace(",", ""))

    return decision


# ---------------------------------------------------------------------------
# Order builder — returns a dict describing the order to place
# ---------------------------------------------------------------------------

def build_order(
    ticker: str,
    decision: dict,
    account_equity: float,
    current_price: float | None = None,
) -> dict | None:
    """Map a parsed decision to an order spec dict.

    Returns None if no order should be placed (Hold / ambiguous).
    Keys in the returned dict:
      symbol, side ('buy'|'sell'), otype ('market'|'limit'),
      limit_price (float, for limit), notional (float, for market),
      qty (int, for limit), stop_price (float, optional)
    """
    action = (decision.get("action") or "").upper()
    rating = (decision.get("rating") or "").upper()

    # Determine direction: trader's action is primary, PM rating is fallback
    direction = None
    if action == "BUY":
        direction = "buy"
    elif action == "SELL":
        direction = "sell"
    elif action in ("HOLD", ""):
        # Trader held or gave no action → fall back to PM rating
        if rating in ("BUY", "OVERWEIGHT"):
            direction = "buy"
        elif rating in ("SELL", "UNDERWEIGHT"):
            direction = "sell"
        elif rating in ("HOLD", "REVIEW", ""):
            return None
    if direction is None:
        return None

    # Sizing (default 5% of equity when the trader gave no parseable hint)
    notional = parse_position_sizing(decision.get("position_sizing"), account_equity)

    # Price: prefer the trader's entry price, fall back to current market
    ref_price = decision.get("entry_price") or current_price
    stop_price = decision.get("stop_loss")
    take_profit = decision.get("price_target")

    # --- MARKET order (no reference price available) ---
    if ref_price is None:
        return {
            "symbol": ticker,
            "side": direction,
            "otype": "market",
            "notional": round(notional, 2) if notional else None,
            "limit_price": None,
            "qty": None,
            "stop_price": None,
            "take_profit_price": None,
        }

    # --- LIMIT order (we have a reference price) ---
    qty = max(1, int(notional / ref_price)) if (notional and ref_price > 0) else 1
    return {
        "symbol": ticker,
        "side": direction,
        "otype": "limit",
        "limit_price": ref_price,
        "qty": qty,
        "notional": None,
        "stop_price": stop_price,
        "take_profit_price": take_profit,
    }


# ---------------------------------------------------------------------------
# Order Submitter
# ---------------------------------------------------------------------------

def submit_alpaca_order(order: dict, client: TradingClient | None = None) -> dict[str, Any]:
    """Submit an order to Alpaca and return structured execution result."""
    if client is None:
        client = get_alpaca_trading_client()

    side = OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL

    if order["otype"] == "market":
        req = MarketOrderRequest(
            symbol=order["symbol"],
            notional=order.get("notional"),
            side=side,
            time_in_force="day",
        )
    else:
        kwargs: dict[str, Any] = dict(
            symbol=order["symbol"],
            qty=order.get("qty"),
            side=side,
            time_in_force="day",
            limit_price=order.get("limit_price"),
        )
        has_stop = order.get("stop_price") is not None
        has_target = order.get("take_profit_price") is not None

        if has_stop and has_target:
            kwargs["order_class"] = OrderClass.BRACKET
            kwargs["stop_loss"] = StopLossRequest(stop_price=order["stop_price"])
            kwargs["take_profit"] = TakeProfitRequest(limit_price=order["take_profit_price"])
        elif has_stop:
            logger.warning(
                "Stop price $%.2f noted for %s but bracket requires target; submitting limit only",
                order["stop_price"],
                order["symbol"],
            )
        req = LimitOrderRequest(**kwargs)

    placed = client.submit_order(req)
    return {
        "id": str(placed.id),
        "status": str(placed.status),
        "client_order_id": getattr(placed, "client_order_id", None),
        "symbol": str(placed.symbol),
        "qty": str(placed.qty) if getattr(placed, "qty", None) else None,
        "filled_qty": str(placed.filled_qty) if getattr(placed, "filled_qty", None) else None,
        "filled_avg_price": float(str(placed.filled_avg_price)) if getattr(placed, "filled_avg_price", None) else None,
        "side": str(placed.side),
        "order_type": str(placed.order_type),
    }


# ---------------------------------------------------------------------------
# Main CLI execution (backward-compatible with execute_order.py)
# ---------------------------------------------------------------------------

def execute(
    ticker: str,
    trade_date: str | None = None,
    dry_run: bool = False,
    from_log: bool = False,
):
    """Execute TradingAgents pipeline or saved log, and optionally place Alpaca order."""
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    # -- Step 1: Get the decision state --------------------------------------
    if from_log:
        log_path = (
            PROJECT_ROOT
            / "results"
            / ticker
            / "TradingAgentsStrategy_logs"
            / f"full_states_log_{trade_date}.json"
        )
        if not log_path.exists():
            print(f"❌ No saved state found at {log_path}")
            sys.exit(1)
        print(f"📂 Loading from {log_path}")
        with open(log_path, encoding="utf-8") as f:
            final_state = json.load(f)
        signal = "(from saved log)"
    else:
        print(f"\n🧠 Running TradingAgents for {ticker} on {trade_date}...")
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        config = DEFAULT_CONFIG.copy()
        ta = TradingAgentsGraph(config=config)

        # Inject portfolio context from Alpaca if available
        portfolio = get_alpaca_portfolio_context()
        final_state, signal = ta.propagate(ticker, trade_date, portfolio=portfolio)
        print(f"   Signal: {signal}")

    # -- Step 2: Parse the decision ------------------------------------------
    print(f"\n📋 Parsed decision:")
    decision = parse_trader_decision(final_state)
    for k, v in decision.items():
        if v:
            print(f"   {k}: {v}")
    if not decision.get("action") and not decision.get("rating"):
        print("   ⚠️  No actionable signal — skipping.")
        return

    # -- Step 3: Connect to Alpaca --------------------------------------------
    api_key, secret_key, paper = get_alpaca_credentials()
    client = TradingClient(api_key, secret_key, paper=paper)

    acct = client.get_account()
    equity = float(str(acct.equity))
    label = "paper" if paper else "⚠️ LIVE"
    print(f"\n💰 Alpaca {label} account: ${equity:,.2f} equity")

    # Fetch last close price for market-order fallback / sizing (best-effort)
    current_price = fetch_last_close_price(ticker)
    if current_price:
        print(f"   Last close: ${current_price:.2f}")

    # -- Step 4: Build the order ----------------------------------------------
    print(f"\n📦 Building order...")
    order = build_order(ticker, decision, equity, current_price)

    if order is None:
        print("   → HOLD / REVIEW / ambiguous: no order placed.")
        return

    print(f"   Symbol:   {order['symbol']}")
    print(f"   Side:     {order['side']}")
    print(f"   Type:     {order['otype']}")
    if order.get("limit_price"):
        print(f"   Limit:    ${order['limit_price']:.2f}")
    if order.get("notional"):
        print(f"   Notional: ${order['notional']:,.2f}")
    if order.get("qty"):
        print(f"   Qty:      {order['qty']} shares")
    if order.get("stop_price"):
        print(f"   Stop:     ${order['stop_price']:.2f}")
    if order.get("take_profit_price"):
        print(f"   Target:   ${order['take_profit_price']:.2f}")

    if dry_run:
        print(f"\n🔍 DRY RUN — order NOT submitted.")
        return

    # -- Step 5: Submit the order ---------------------------------------------
    print(f"\n🚀 Submitting order to Alpaca {label}...")
    try:
        placed = submit_alpaca_order(order, client=client)
        print(f"   ✅ Order submitted!")
        print(f"      ID:       {placed['id']}")
        print(f"      Status:   {placed['status']}")
        if placed.get("client_order_id"):
            print(f"      ClientID: {placed['client_order_id']}")
        if placed.get("filled_qty"):
            print(f"      Filled:   {placed['filled_qty']} shares")
            if placed.get("filled_avg_price"):
                print(f"      Avg Price: ${placed['filled_avg_price']:,.2f}")
    except Exception as e:
        print(f"   ❌ Order submission failed: {e}")
        return

    # -- Step 6: Show post-order state -----------------------------------------
    print(f"\n📊 Post-order state:")
    try:
        positions = client.get_all_positions()
        if positions:
            for p in positions:
                print(f"   {p.symbol}: {p.qty} shares @ ${float(str(p.avg_entry_price)):,.2f}")
        else:
            print("   (flat — no open positions)")
    except Exception:
        pass

    acct2 = client.get_account()
    print(
        f"\n💰 Account: equity=${float(str(acct2.equity)):,.2f}"
        f"  cash=${float(str(acct2.cash)):,.2f}"
        f"  BP=${float(str(acct2.buying_power)):,.2f}"
    )


def main():
    parser = argparse.ArgumentParser(description="TradingAgents → Alpaca execution bridge")
    parser.add_argument("ticker", help="Ticker symbol (e.g. NVDA, AAPL, QQQ)")
    parser.add_argument("date", nargs="?", default=None,
                        help="Trade date YYYY-MM-DD (defaults to today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show the order without submitting")
    parser.add_argument("--from-log", action="store_true",
                        help="Parse from a saved state log instead of re-running the agent")
    args = parser.parse_args()

    if args.date is None:
        args.date = datetime.now().strftime("%Y-%m-%d")

    execute(args.ticker, args.date, dry_run=args.dry_run, from_log=args.from_log)
