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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide
from alpaca.trading.requests import (
    GetOrderByIdRequest,
    GetOrdersRequest,
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
    """Fetch latest trade/close price from Alpaca data API for reference/sizing (best effort)."""
    api_key, secret_key, _ = get_alpaca_credentials()
    if not api_key or not secret_key:
        return None
    try:
        from alpaca.data import StockHistoricalDataClient
        from alpaca.data.requests import StockLatestTradeRequest

        sym = ticker.strip().upper()
        client = StockHistoricalDataClient(api_key, secret_key)
        resp = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=sym))
        trade = None
        if resp and sym in resp:
            trade = resp[sym]
        elif resp and ticker in resp:
            trade = resp[ticker]

        if trade is not None:
            price = getattr(trade, "price", None)
            if price is None and isinstance(trade, dict):
                price = trade.get("price")
            if price is not None:
                return float(price)
    except Exception as e:
        logger.warning("Skipped price lookup for %s: %s", ticker, e)
    return None


def get_stock_quote(symbol: str, client: Any = None) -> dict[str, Any] | None:
    """Fetch current price, previous close, and daily change percentage for a ticker symbol."""
    sym = symbol.strip().upper()
    if not sym:
        return None
    try:
        if client is None:
            api_key, secret_key, _ = get_alpaca_credentials()
            if not api_key or not secret_key:
                return None
            from alpaca.data import StockHistoricalDataClient
            client = StockHistoricalDataClient(api_key, secret_key)

        from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
        from alpaca.data.timeframe import TimeFrame

        current_price: float | None = None

        # Fetch recent daily bars for previous close and fallback current price
        bars = None
        try:
            req = StockBarsRequest(symbol_or_symbols=sym, timeframe=TimeFrame.Day, limit=3)
            bars_resp = client.get_stock_bars(req)
            if bars_resp is not None:
                if hasattr(bars_resp, "__getitem__"):
                    try:
                        if sym in bars_resp:
                            bars = bars_resp[sym]
                        elif symbol in bars_resp:
                            bars = bars_resp[symbol]
                    except Exception:
                        pass
                if bars is None and hasattr(bars_resp, "data") and isinstance(bars_resp.data, dict):
                    bars = bars_resp.data.get(sym) or bars_resp.data.get(symbol)
                if bars is None and isinstance(bars_resp, dict):
                    bars = bars_resp.get(sym) or bars_resp.get(symbol)
        except Exception as e:
            logger.warning("Bars lookup failed for %s: %s", sym, e)

        def _extract_close(b: Any) -> float | None:
            if b is None:
                return None
            for attr in ("close", "c"):
                val = getattr(b, attr, None)
                if val is not None:
                    try:
                        return float(val)
                    except (ValueError, TypeError):
                        pass
            if isinstance(b, dict):
                for key in ("close", "c"):
                    val = b.get(key)
                    if val is not None:
                        try:
                            return float(val)
                        except (ValueError, TypeError):
                            pass
            return None

        prev_close: float | None = None
        if bars and len(bars) >= 2:
            prev_close = _extract_close(bars[-2])

        # Attempt to get latest trade price first
        try:
            if hasattr(client, "get_stock_latest_trade"):
                trade_resp = client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=sym))
                trade = None
                if trade_resp and sym in trade_resp:
                    trade = trade_resp[sym]
                elif trade_resp and symbol in trade_resp:
                    trade = trade_resp[symbol]

                if trade is not None:
                    price = getattr(trade, "price", None)
                    if price is None and isinstance(trade, dict):
                        price = trade.get("price")
                    if price is not None:
                        current_price = float(price)
        except Exception as e:
            logger.debug("Latest trade lookup skipped for %s: %s", sym, e)

        # Fallback current_price to the most recent bar close
        if current_price is None and bars and len(bars) >= 1:
            current_price = _extract_close(bars[-1])

        if current_price is None:
            return None

        change_pct: float | None = None
        if prev_close:
            change_pct = round(((current_price - prev_close) / prev_close) * 100.0, 4)

        return {
            "symbol": sym,
            "current_price": current_price,
            "prev_close": prev_close,
            "change_pct": change_pct,
        }
    except Exception as e:
        logger.warning("Failed to get stock quote for %s: %s", symbol, e)
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
    held_qty: float | None = None,
) -> dict | None:
    """Map a parsed decision to an order spec dict.

    Returns None for Hold / ambiguous decisions or sells without long holdings.
    Keys in the returned dict:
      symbol, side ('buy'|'sell'), otype ('market'|'limit'),
      limit_price (float, for limit), notional (float, for market),
      qty (int, for limit), stop_price (float, optional),
      take_profit_price (float, optional)
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

    if direction == "sell" and (held_qty is None or held_qty <= 0):
        return None

    # Sizing (default 5% of equity when the trader gave no parseable hint)
    notional = parse_position_sizing(decision.get("position_sizing"), account_equity)

    # Fetch market price if not provided
    if current_price is None:
        current_price = fetch_last_close_price(ticker)

    llm_entry = decision.get("entry_price")
    stop_price = decision.get("stop_loss")
    take_profit = decision.get("price_target")

    # Quantity-based sells cannot exceed holdings, including market fallbacks.
    market_sell_qty = None
    if direction == "sell":
        market_sell_qty = held_qty
        if current_price and current_price > 0 and notional:
            market_sell_qty = min(held_qty, max(1, int(notional / current_price)))

    # If current market price is unavailable (API failure), fall back to market order
    if current_price is None:
        logger.warning("⚠️ Current market price unavailable for %s — using market order", ticker)
        return {
            "symbol": ticker,
            "side": direction,
            "otype": "market",
            "notional": None if direction == "sell" else (round(notional, 2) if notional else None),
            "limit_price": None,
            "qty": market_sell_qty,
            "stop_price": None,
            "take_profit_price": None,
        }

    # If no LLM entry price was provided, fall back to market order
    if llm_entry is None:
        return {
            "symbol": ticker,
            "side": direction,
            "otype": "market",
            "notional": None if direction == "sell" else (round(notional, 2) if notional else None),
            "limit_price": None,
            "qty": market_sell_qty,
            "stop_price": None,
            "take_profit_price": None,
        }

    # Validate LLM entry price against current_price:
    # If deviation > 25%, treat LLM price as unreliable -> fallback to MARKET order
    deviation = abs(llm_entry - current_price) / current_price
    if deviation > 0.25:
        msg = f"⚠️ LLM entry price ${llm_entry:.2f} deviates >25% from market ${current_price:.2f} — using market order"
        logger.warning(msg)
        print(msg)
        return {
            "symbol": ticker,
            "side": direction,
            "otype": "market",
            "notional": None if direction == "sell" else (round(notional, 2) if notional else None),
            "limit_price": None,
            "qty": market_sell_qty,
            "stop_price": None,
            "take_profit_price": None,
        }

    # LLM price is within 25% of current_price -> LIMIT order with LLM price
    ref_price = llm_entry

    # Validate stop_loss and price_target consistency relative to ref_price
    if direction == "buy":
        # Check if stop and target were swapped by LLM
        if (
            stop_price is not None
            and take_profit is not None
            and stop_price > ref_price
            and take_profit < ref_price
        ):
            logger.warning(
                "⚠️ LLM swapped stop_loss ($%.2f) and price_target ($%.2f) for buy — swapping back",
                stop_price,
                take_profit,
            )
            stop_price, take_profit = take_profit, stop_price

        # For buy, stop_loss must be below entry price
        if stop_price is not None and (stop_price >= ref_price or stop_price <= 0):
            old_stop = stop_price
            stop_price = round(ref_price * 0.93, 2)
            logger.warning(
                "⚠️ LLM stop_loss $%.2f inconsistent with buy entry $%.2f — adjusted to $%.2f",
                old_stop,
                ref_price,
                stop_price,
            )

        # For buy, price_target must be above entry price
        if take_profit is not None and take_profit <= ref_price:
            old_tp = take_profit
            take_profit = round(ref_price * 1.20, 2)
            logger.warning(
                "⚠️ LLM price_target $%.2f inconsistent with buy entry $%.2f — adjusted to $%.2f",
                old_tp,
                ref_price,
                take_profit,
            )
    else:  # sell
        # Check if stop and target were swapped by LLM
        if (
            stop_price is not None
            and take_profit is not None
            and stop_price < ref_price
            and take_profit > ref_price
        ):
            logger.warning(
                "⚠️ LLM swapped stop_loss ($%.2f) and price_target ($%.2f) for sell — swapping back",
                stop_price,
                take_profit,
            )
            stop_price, take_profit = take_profit, stop_price

        # For sell, stop_loss must be above entry price
        if stop_price is not None and stop_price <= ref_price:
            old_stop = stop_price
            stop_price = round(ref_price * 1.07, 2)
            logger.warning(
                "⚠️ LLM stop_loss $%.2f inconsistent with sell entry $%.2f — adjusted to $%.2f",
                old_stop,
                ref_price,
                stop_price,
            )

        # For sell, price_target must be below entry price
        if take_profit is not None and (take_profit >= ref_price or take_profit <= 0):
            old_tp = take_profit
            take_profit = round(ref_price * 0.80, 2)
            logger.warning(
                "⚠️ LLM price_target $%.2f inconsistent with sell entry $%.2f — adjusted to $%.2f",
                old_tp,
                ref_price,
                take_profit,
            )

    qty = max(1, int(notional / ref_price)) if (notional and ref_price > 0) else 1
    if direction == "sell":
        qty = min(qty, held_qty)
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
        mkt_kwargs: dict[str, Any] = {
            "symbol": order["symbol"],
            "side": side,
            "time_in_force": "day",
        }
        if order.get("qty"):
            mkt_kwargs["qty"] = order["qty"]
        else:
            mkt_kwargs["notional"] = order.get("notional")
        req = MarketOrderRequest(**mkt_kwargs)
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
# Active Orders Management (Alpaca Live Orders)
# ---------------------------------------------------------------------------

def _format_order(o: Any) -> dict[str, Any]:
    """Format an Alpaca Order object into a standard dictionary."""
    side_val = getattr(o, "side", None)
    if hasattr(side_val, "value"):
        side_val = side_val.value
    status_val = getattr(o, "status", None)
    if hasattr(status_val, "value"):
        status_val = status_val.value
    order_class_val = getattr(o, "order_class", None)
    if hasattr(order_class_val, "value"):
        order_class_val = order_class_val.value
    order_type_val = getattr(o, "order_type", None)
    if hasattr(order_type_val, "value"):
        order_type_val = order_type_val.value
    tif_val = getattr(o, "time_in_force", None)
    if hasattr(tif_val, "value"):
        tif_val = tif_val.value

    limit_price = (
        float(str(o.limit_price))
        if getattr(o, "limit_price", None) is not None
        else None
    )
    stop_price = (
        float(str(o.stop_price))
        if getattr(o, "stop_price", None) is not None
        else None
    )
    take_profit = None
    if getattr(o, "take_profit_price", None) is not None:
        take_profit = float(str(o.take_profit_price))
    elif getattr(o, "take_profit", None) is not None:
        tp = o.take_profit
        take_profit = float(str(getattr(tp, "limit_price", tp)))

    # If stop or take_profit not on parent, extract from child legs (e.g. bracket orders)
    legs = getattr(o, "legs", None)
    if legs and isinstance(legs, list):
        for leg in legs:
            leg_type = str(
                getattr(leg, "order_type", None)
                or (leg.get("order_type") if isinstance(leg, dict) else "")
            ).lower()
            leg_stop = getattr(leg, "stop_price", None) or (
                leg.get("stop_price") if isinstance(leg, dict) else None
            )
            leg_limit = getattr(leg, "limit_price", None) or (
                leg.get("limit_price") if isinstance(leg, dict) else None
            )
            if leg_stop is not None and stop_price is None:
                stop_price = float(str(leg_stop))
            if "limit" in leg_type and leg_limit is not None and take_profit is None:
                take_profit = float(str(leg_limit))

    created_at = (
        o.created_at.isoformat()
        if hasattr(getattr(o, "created_at", None), "isoformat")
        else str(o.created_at)
        if getattr(o, "created_at", None)
        else None
    )

    return {
        "id": str(o.id),
        "symbol": str(o.symbol),
        "side": str(side_val).lower() if side_val else None,
        "qty": float(str(o.qty)) if getattr(o, "qty", None) is not None else None,
        "filled_qty": float(str(o.filled_qty)) if getattr(o, "filled_qty", None) is not None else 0.0,
        "limit_price": limit_price,
        "stop_price": stop_price,
        "take_profit": take_profit,
        "status": str(status_val).lower() if status_val else None,
        "created_at": created_at,
        "time_in_force": str(tif_val).lower() if tif_val else None,
        "order_class": str(order_class_val).lower() if order_class_val else None,
        "order_type": str(order_type_val).lower() if order_type_val else None,
        "client_order_id": getattr(o, "client_order_id", None),
    }


def get_live_orders(client: TradingClient | None = None) -> list[dict[str, Any]]:
    """Fetch all open/pending orders from Alpaca.

    Uses GetOrdersRequest(status='open') — in alpaca-py v0.44 the status param
    accepts 'open', 'closed', or 'all' (string values, NOT OrderStatus enums).
    """
    if client is None:
        client = get_alpaca_trading_client()

    req = GetOrdersRequest(status="open", nested=True)
    orders = client.get_orders(req)
    return [_format_order(o) for o in orders]


def get_live_order(order_id: str, client: TradingClient | None = None) -> dict[str, Any] | None:
    """Fetch a single order by its Alpaca order ID."""
    if client is None:
        client = get_alpaca_trading_client()
    try:
        order = client.get_order_by_id(order_id, filter=GetOrderByIdRequest(nested=True))
        return _format_order(order)
    except Exception as e:
        logger.warning("Could not fetch Alpaca order %s: %s", order_id, e)
        return None


def cancel_live_order(order_id: str, client: TradingClient | None = None) -> bool:
    """Cancel an open order by its Alpaca order ID."""
    if client is None:
        client = get_alpaca_trading_client()

    called = False
    if hasattr(client, "cancel_order_by_id"):
        client.cancel_order_by_id(order_id)
        called = True
    if hasattr(client, "cancel_order") and type(client) is not TradingClient:
        client.cancel_order(order_id)
        called = True

    if not called:
        raise AttributeError("TradingClient has no order cancel method")
    return True


# ---------------------------------------------------------------------------
# Recommendation Execution Bridge (Web App / API)
# ---------------------------------------------------------------------------

def execute_recommendation(
    recommendation: dict[str, Any],
    run_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    client: TradingClient | None = None,
) -> dict[str, Any]:
    """Execute a recommendation by submitting a bracket/limit order to Alpaca.

    Parameters:
      recommendation: dict representing a recommendation row from the DB.
      run_id: optional run ID to link order and log output.
      overrides: optional dict with user adjustments (qty, limit_price, stop_price,
                 take_profit_price, order_type, side).
      client: optional pre-configured Alpaca TradingClient.

    Returns:
      dict with execution outcome details.
    """
    from webapp.db import append_run_log, get_order_by_run, record_order_execution

    overrides = overrides or {}
    ticker = (recommendation.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("Recommendation does not have a valid ticker symbol")

    action = (recommendation.get("action") or "").strip().upper()
    rating = (recommendation.get("rating") or "").strip().upper()

    # Determine side — HOLD/REVIEW is absolute: reject immediately regardless of overrides.
    _is_non_actionable = (
        action in ("HOLD", "REVIEW", "")
        and rating in ("HOLD", "REVIEW", "NEUTRAL", "")
    )
    if _is_non_actionable:
        raise ValueError(
            f"Cannot execute order: recommendation is '{rating or 'HOLD'}' (non-actionable). "
            f"A HOLD/REVIEW rating cannot be converted to a trade."
        )

    # For actionable ratings, an explicit side override takes precedence
    if overrides.get("side"):
        side = overrides["side"].strip().lower()
    elif action == "BUY":
        side = "buy"
    elif action == "SELL":
        side = "sell"
    elif not action and rating in ("BUY", "OVERWEIGHT"):
        side = "buy"
    elif not action and rating in ("SELL", "UNDERWEIGHT"):
        side = "sell"
    else:
        raise ValueError(
            f"Cannot execute order: recommendation action '{action or rating}' is non-actionable (HOLD/REVIEW)"
        )

    # Initialize Alpaca client
    if client is None:
        client = get_alpaca_trading_client()

    acct_info = get_account_overview(client)
    account_equity = float(acct_info.get("equity", 100000.0))

    positions = acct_info.get("positions", [])
    pos = next(
        (p for p in positions if str(p.get("symbol", "")).strip().upper() == ticker),
        None,
    )
    held_qty = 0.0
    if pos:
        pos_side = str(pos.get("side", "")).lower()
        if "short" not in pos_side:
            try:
                held_qty = float(pos.get("qty", 0.0))
            except (ValueError, TypeError):
                held_qty = 0.0

    actual_run_id = run_id or recommendation.get("run_id")

    # Check if there is an existing order record in DB for this run
    existing_order = None
    if actual_run_id:
        existing_order = get_order_by_run(actual_run_id)

    # Build decision dict
    decision = {
        "action": action,
        "rating": rating,
        "entry_price": recommendation.get("entry_price"),
        "stop_loss": recommendation.get("stop_loss"),
        "price_target": recommendation.get("price_target"),
        "position_sizing": recommendation.get("position_sizing"),
        "reasoning": recommendation.get("reasoning"),
    }

    current_price = fetch_last_close_price(ticker)
    built_spec = build_order(ticker, decision, account_equity, current_price, held_qty=held_qty)

    # Start with built spec or create baseline
    if built_spec is not None:
        order_spec = built_spec.copy()
    else:
        ref_price = recommendation.get("entry_price") or current_price or 100.0
        notional = parse_position_sizing(recommendation.get("position_sizing"), account_equity)
        qty = max(1, int(notional / ref_price)) if ref_price > 0 else 1
        order_spec = {
            "symbol": ticker,
            "side": side,
            "otype": "limit",
            "limit_price": ref_price,
            "qty": qty,
            "notional": None,
            "stop_price": recommendation.get("stop_loss"),
            "take_profit_price": recommendation.get("price_target"),
        }

    # If existing order record had specific quantities or prices and built spec defaulted, use existing
    if existing_order:
        if existing_order.get("qty") and not overrides.get("qty"):
            order_spec["qty"] = existing_order["qty"]
        if existing_order.get("limit_price") and not overrides.get("limit_price"):
            order_spec["limit_price"] = existing_order["limit_price"]
        if existing_order.get("stop_price") and not overrides.get("stop_price"):
            order_spec["stop_price"] = existing_order["stop_price"]
        if existing_order.get("take_profit_price") and not overrides.get("take_profit_price"):
            order_spec["take_profit_price"] = existing_order["take_profit_price"]

    # Apply overrides
    if overrides.get("qty") is not None:
        order_spec["qty"] = int(overrides["qty"])
    if overrides.get("limit_price") is not None:
        order_spec["limit_price"] = float(overrides["limit_price"])
    if overrides.get("stop_price") is not None:
        order_spec["stop_price"] = float(overrides["stop_price"])
    if overrides.get("take_profit_price") is not None:
        order_spec["take_profit_price"] = float(overrides["take_profit_price"])
    if overrides.get("order_type") is not None:
        order_spec["otype"] = str(overrides["order_type"]).lower()
    if overrides.get("side") is not None:
        order_spec["side"] = str(overrides["side"]).lower()

    # Validate limit order requirements
    if order_spec["otype"] == "limit":
        if not order_spec.get("limit_price"):
            order_spec["limit_price"] = recommendation.get("entry_price") or current_price or 100.0
        if not order_spec.get("qty") or order_spec["qty"] <= 0:
            ref_p = order_spec.get("limit_price") or 100.0
            order_spec["qty"] = max(1, int((account_equity * 0.05) / ref_p))

        # Re-validate bracket pricing consistency
        ref_p = float(order_spec["limit_price"])
        st = float(order_spec["stop_price"]) if order_spec.get("stop_price") is not None else None
        tp = float(order_spec["take_profit_price"]) if order_spec.get("take_profit_price") is not None else None

        if order_spec["side"] == "buy":
            if st is not None and tp is not None and st > ref_p and tp < ref_p:
                st, tp = tp, st
            if st is not None and (st >= ref_p or st <= 0):
                st = round(ref_p * 0.93, 2)
            if tp is not None and tp <= ref_p:
                tp = round(ref_p * 1.20, 2)
        else:  # sell
            if st is not None and tp is not None and st < ref_p and tp > ref_p:
                st, tp = tp, st
            if st is not None and st <= ref_p:
                st = round(ref_p * 1.07, 2)
            if tp is not None and (tp >= ref_p or tp <= 0):
                tp = round(ref_p * 0.80, 2)

        order_spec["stop_price"] = st
        order_spec["take_profit_price"] = tp

    # Position validation check for sell orders: prevent selling unheld stocks
    if order_spec["side"] == "sell":

        if held_qty <= 0:
            err_msg = f"Cannot sell {ticker}: you do not hold a position in {ticker}"
            if actual_run_id:
                now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                append_run_log(actual_run_id, f"[{now_str}] ❌ Order execution rejected: {err_msg}\n")
            raise ValueError(err_msg)

        if order_spec.get("qty") is not None:
            order_spec["qty"] = min(int(order_spec["qty"]), int(held_qty))
            if order_spec["qty"] <= 0:
                err_msg = f"Cannot sell {ticker}: you do not hold a position in {ticker}"
                if actual_run_id:
                    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                    append_run_log(actual_run_id, f"[{now_str}] ❌ Order execution rejected: {err_msg}\n")
                raise ValueError(err_msg)

    # Submit to Alpaca
    rec_id = recommendation.get("id")

    try:
        submitted = submit_alpaca_order(order_spec, client=client)
    except Exception as exc:
        err_msg = str(exc)
        logger.error("Alpaca submission failed for rec %s: %s", rec_id, err_msg)
        if actual_run_id:
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
            append_run_log(actual_run_id, f"[{now_str}] ❌ Manual order submission failed: {err_msg}\n")
            record_order_execution(
                run_id=actual_run_id,
                recommendation_id=rec_id,
                ticker=order_spec["symbol"],
                side=order_spec["side"],
                order_type=order_spec["otype"],
                qty=order_spec.get("qty"),
                notional=order_spec.get("notional"),
                limit_price=order_spec.get("limit_price"),
                stop_price=order_spec.get("stop_price"),
                take_profit_price=order_spec.get("take_profit_price"),
                status="failed",
                error_message=err_msg,
            )
        raise

    # Submission succeeded: record in DB
    order_db_id = None
    if actual_run_id:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        append_run_log(
            actual_run_id,
            f"[{now_str}] ⚡ Manual execution confirmed: Submitted {order_spec['side'].upper()} "
            f"{order_spec.get('qty')} shs @ ${order_spec.get('limit_price') or 0:.2f} "
            f"to Alpaca (Order ID: {submitted.get('id')})\n",
        )
        order_db_id = record_order_execution(
            run_id=actual_run_id,
            recommendation_id=rec_id,
            ticker=order_spec["symbol"],
            side=order_spec["side"],
            order_type=order_spec["otype"],
            qty=order_spec.get("qty"),
            notional=order_spec.get("notional"),
            limit_price=order_spec.get("limit_price"),
            stop_price=order_spec.get("stop_price"),
            take_profit_price=order_spec.get("take_profit_price"),
            status="submitted",
            alpaca_order_id=submitted.get("id"),
            client_order_id=submitted.get("client_order_id"),
            raw_response=submitted,
        )
        try:
            from webapp.db import update_run_status
            update_run_status(actual_run_id, "completed")
        except Exception as e:
            logger.warning("Could not update run status to completed for run %s: %s", actual_run_id, e)

    return {
        "success": True,
        "run_id": actual_run_id,
        "recommendation_id": rec_id,
        "order_id": order_db_id,
        "alpaca_order_id": submitted.get("id"),
        "client_order_id": submitted.get("client_order_id"),
        "status": submitted.get("status"),
        "symbol": order_spec["symbol"],
        "side": order_spec["side"],
        "qty": order_spec.get("qty"),
        "limit_price": order_spec.get("limit_price"),
        "stop_price": order_spec.get("stop_price"),
        "take_profit_price": order_spec.get("take_profit_price"),
        "order_type": order_spec["otype"],
        "message": (
            f"Successfully submitted {order_spec['side'].upper()} order for "
            f"{order_spec.get('qty')} {order_spec['symbol']} to Alpaca"
        ),
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
    held_qty = next((float(p.qty) for p in client.get_all_positions()
                     if p.symbol.upper() == ticker.upper() and "short" not in str(p.side).lower()), 0.0)
    order = build_order(ticker, decision, equity, current_price, held_qty=held_qty)

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
