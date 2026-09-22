"""FastAPI router defining REST endpoints for the TradingAgents Dashboard."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from webapp.db import (
    add_watchlist_item,
    append_run_log,
    delete_run,
    get_all_settings,
    get_llm_calls,
    get_order_by_run,
    get_orders,
    get_recommendation,
    get_recommendation_by_run,
    get_recommendations,
    get_run,
    get_runs,
    get_setting,
    get_watchlist,
    remove_watchlist_item,
    remove_watchlist_items,
    set_setting,
    update_order_status,
    update_watchlist_item,
)
from webapp import llm_settings
from webapp.summary import summarize_run
from webapp.execution import (
    cancel_live_order,
    execute_recommendation,
    get_account_overview,
    get_alpaca_credentials,
    get_live_order,
    get_live_orders,
    get_stock_quote,
)
from webapp.runner import runner
from webapp.symbols import get_symbol_info, search_symbols

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# Pydantic Request Models
# ---------------------------------------------------------------------------

class WatchlistAddRequest(BaseModel):
    symbol: str = Field(..., min_length=1, description="Ticker symbol (e.g. AAPL)")
    notes: str = Field(default="", description="Optional user notes")


class WatchlistUpdateRequest(BaseModel):
    enabled: bool | None = Field(default=None, description="Toggle active state")
    notes: str | None = Field(default=None, description="Update notes")


class WatchlistBatchDeleteRequest(BaseModel):
    symbols: list[str] = Field(..., min_length=1, description="List of ticker symbols to remove")



class SettingsUpdateRequest(BaseModel):
    auto_trade: bool | None = None
    schedule_enabled: bool | None = None
    schedule_interval_minutes: int | None = None


class LLMConfigUpdateRequest(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    deep_model: str | None = None
    quick_model: str | None = None


class RunTriggerRequest(BaseModel):
    ticker: str | None = None
    all_watchlist: bool = False
    trade_date: str | None = None
    from_log_path: str | None = None


class ExecuteRecommendationRequest(BaseModel):
    qty: int | None = Field(default=None, description="Optional custom share quantity")
    limit_price: float | None = Field(default=None, description="Optional limit price override")
    stop_price: float | None = Field(default=None, description="Optional stop loss price override")
    take_profit_price: float | None = Field(default=None, description="Optional take profit price override")
    order_type: str | None = Field(default=None, description="Optional order type: 'limit' or 'market'")
    side: str | None = Field(default=None, description="Optional side override: 'buy' or 'sell'")
    reexecute: bool | None = Field(default=False, description="Allow re-execution if order already submitted")


# ---------------------------------------------------------------------------
# Health & Status
# ---------------------------------------------------------------------------

@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Alpaca Account & Positions
# ---------------------------------------------------------------------------

@router.get("/account")
def get_account() -> dict[str, Any]:
    """Retrieve Alpaca account balance, paper flag, and open positions."""
    try:
        return get_account_overview()
    except Exception as e:
        logger.warning("Could not fetch Alpaca account: %s", e)
        return {
            "status": "UNAVAILABLE",
            "equity": 0.0,
            "cash": 0.0,
            "buying_power": 0.0,
            "currency": "USD",
            "paper": True,
            "positions": [],
            "error": str(e),
        }


@router.get("/positions")
def get_positions() -> list[dict[str, Any]]:
    """Retrieve current open positions from Alpaca."""
    try:
        acct = get_account_overview()
        return acct.get("positions", [])
    except Exception as e:
        logger.warning("Could not fetch Alpaca positions: %s", e)
        return []


# ---------------------------------------------------------------------------
# Symbol Search & Autocomplete
# ---------------------------------------------------------------------------

@router.get("/symbols/search")
@router.get("/symbols")
def search_stock_symbols(
    q: str = Query("", description="Symbol or company name search query"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of results to return"),
) -> list[dict[str, str]]:
    """Search for symbols matching a partial ticker or company name."""
    return search_symbols(query=q, limit=limit)


@router.get("/symbols/{symbol}")
def get_symbol_detail(symbol: str) -> dict[str, str]:
    """Retrieve details for a single ticker symbol."""
    info = get_symbol_info(symbol)
    if not info:
        raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")
    return info


# ---------------------------------------------------------------------------
# Stock Quotes & Market Data
# ---------------------------------------------------------------------------

@router.get("/stocks/quotes")
def get_stock_quotes(
    symbols: str | None = Query(default=None, description="Comma-separated symbols (default: watchlist)"),
) -> dict[str, Any]:
    """Retrieve current price and daily change % for symbols (default: current watchlist)."""
    try:
        if symbols is not None and symbols.strip():
            sym_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        else:
            sym_list = [item["symbol"].strip().upper() for item in get_watchlist() if item.get("symbol")]

        # Deduplicate while preserving order, cap at ~40 symbols
        seen = set()
        unique_syms = []
        for s in sym_list:
            if s not in seen:
                seen.add(s)
                unique_syms.append(s)
        unique_syms = unique_syms[:40]

        if not unique_syms:
            return {}

        client = None
        try:
            api_key, secret_key, _ = get_alpaca_credentials()
            if api_key and secret_key:
                from alpaca.data import StockHistoricalDataClient
                client = StockHistoricalDataClient(api_key, secret_key)
        except Exception as e:
            logger.warning("Could not initialize Alpaca data client for quotes: %s", e)

        results: dict[str, Any] = {}
        for sym in unique_syms:
            try:
                quote = get_stock_quote(sym, client=client)
                if quote is not None:
                    results[sym] = quote
            except Exception as e:
                logger.warning("Failed to fetch quote for %s: %s", sym, e)

        return results
    except Exception as e:
        logger.error("Error in get_stock_quotes: %s", e)
        return {}


# ---------------------------------------------------------------------------
# Watchlist Management
# ---------------------------------------------------------------------------

@router.get("/watchlist")
def list_watchlist() -> list[dict[str, Any]]:
    items = get_watchlist()
    # Annotate with whether the symbol is currently in flight
    in_flight = set(runner.get_in_flight_tickers())
    for item in items:
        item["in_flight"] = item["symbol"] in in_flight
    return items


@router.post("/watchlist")
def add_watchlist(req: WatchlistAddRequest) -> dict[str, Any]:
    try:
        return add_watchlist_item(req.symbol, req.notes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/watchlist/{symbol}")
def delete_watchlist(symbol: str) -> dict[str, bool]:
    success = remove_watchlist_item(symbol)
    if not success:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    return {"deleted": True}


@router.post("/watchlist/batch-delete")
@router.delete("/watchlist")
def batch_delete_watchlist(req: WatchlistBatchDeleteRequest) -> dict[str, Any]:
    deleted_count = remove_watchlist_items(req.symbols)
    return {
        "deleted": True,
        "count": deleted_count,
        "symbols": [s.strip().upper() for s in req.symbols if s.strip()],
    }



@router.patch("/watchlist/{symbol}")
def update_watchlist(symbol: str, req: WatchlistUpdateRequest) -> dict[str, Any]:
    updated = update_watchlist_item(symbol, enabled=req.enabled, notes=req.notes)
    if not updated:
        raise HTTPException(status_code=404, detail="Symbol not found in watchlist")
    return updated


# ---------------------------------------------------------------------------
# Settings Management
# ---------------------------------------------------------------------------

@router.get("/settings")
def get_settings() -> dict[str, Any]:
    raw = get_all_settings()
    return {
        "auto_trade": raw.get("auto_trade", "false").lower() in ("true", "1", "yes", "on"),
        "schedule_enabled": raw.get("schedule_enabled", "false").lower() in ("true", "1", "yes", "on"),
        "schedule_interval_minutes": int(raw.get("schedule_interval_minutes", "1440")),
        "last_scheduled_run": raw.get("last_scheduled_run", ""),
    }


@router.post("/settings")
def update_settings(req: SettingsUpdateRequest) -> dict[str, Any]:
    if req.auto_trade is not None:
        set_setting("auto_trade", "true" if req.auto_trade else "false")
    if req.schedule_enabled is not None:
        set_setting("schedule_enabled", "true" if req.schedule_enabled else "false")
    if req.schedule_interval_minutes is not None:
        set_setting("schedule_interval_minutes", str(req.schedule_interval_minutes))
    return get_settings()


# ---------------------------------------------------------------------------
# LLM Provider Configuration
# ---------------------------------------------------------------------------

@router.get("/llm-config")
def get_llm_config() -> dict[str, Any]:
    """Return the effective LLM provider configuration (API key masked)."""
    return llm_settings.get_llm_config_public()


@router.post("/llm-config")
def update_llm_config(req: LLMConfigUpdateRequest) -> dict[str, Any]:
    """Persist LLM provider settings; a provider change rewrites the provider-scoped fields."""
    if req.provider is not None and (req.provider == "" or req.provider in llm_settings.LLM_PROVIDERS):
        set_setting(llm_settings.SETTING_PROVIDER, req.provider.strip())
        # Switching provider: clear key/URL/models that belonged to the old provider.
        for k in (llm_settings.SETTING_API_KEY, llm_settings.SETTING_BASE_URL,
                  llm_settings.SETTING_DEEP_MODEL, llm_settings.SETTING_QUICK_MODEL):
            if get_setting(k) is not None:
                set_setting(k, "")
    if req.api_key is not None:
        set_setting(llm_settings.SETTING_API_KEY, req.api_key.strip())
    if req.base_url is not None:
        set_setting(llm_settings.SETTING_BASE_URL, req.base_url.strip())
    if req.deep_model is not None:
        set_setting(llm_settings.SETTING_DEEP_MODEL, req.deep_model.strip())
    if req.quick_model is not None:
        set_setting(llm_settings.SETTING_QUICK_MODEL, req.quick_model.strip())
    return get_llm_config()


class LLMTestRequest(BaseModel):
    provider: str
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


@router.post("/llm-config/test")
def test_llm_config(req: LLMTestRequest) -> dict[str, Any]:
    """Make one throwaway call to verify a provider/key/model combination."""
    try:
        api_key = req.api_key
        if not api_key:
            saved = llm_settings.get_llm_config()
            if saved["provider"] == req.provider.strip().lower():
                api_key = saved["api_key"]
        message = llm_settings.test_connection(
            provider=req.provider,
            api_key=api_key,
            base_url=req.base_url,
            model=req.model,
        )
    except Exception as e:  # noqa: BLE001 - surface the provider's real error message
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True, "message": message}


# ---------------------------------------------------------------------------
# Runs & History
# ---------------------------------------------------------------------------

@router.get("/runs")
def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    ticker: str | None = None,
) -> list[dict[str, Any]]:
    return get_runs(limit=limit, ticker=ticker)


@router.get("/runs/in-flight")
def get_in_flight() -> list[str]:
    return runner.get_in_flight_tickers()


@router.get("/runs/in-flight-detail")
def get_in_flight_detail() -> list[dict[str, Any]]:
    """Retrieve active in-flight runs with active LangGraph node and live LLM/tool calls."""
    return runner.get_in_flight_details()


@router.get("/runs/{run_id}")
def get_run_detail(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    # Merge live memory logs if still running
    mem_log = runner.get_run_memory_logs(run_id) or ""
    current_logs = run.get("log_output") or ""
    if len(mem_log) > len(current_logs):
        run["log_output"] = mem_log
    return run


@router.delete("/runs/{run_id}")
def delete_run_history(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] == "running" or any(
        item["run_id"] == run_id for item in runner.get_in_flight_details()
    ):
        raise HTTPException(status_code=400, detail="Cannot delete a running run")
    if not delete_run(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"deleted": True, "run_id": run_id}


@router.get("/runs/{run_id}/logs")
def get_run_logs(run_id: str) -> dict[str, str]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    logs = run.get("log_output") or ""
    mem_log = runner.get_run_memory_logs(run_id) or ""
    if len(mem_log) > len(logs):
        logs = mem_log
    return {"logs": logs}


@router.get("/runs/{run_id}/llm-calls")
def get_run_llm_calls(run_id: str) -> list[dict[str, Any]]:
    """Retrieve ordered list of captured LLM and tool calls for a run."""
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    calls = get_llm_calls(run_id)
    if not calls:
        calls = runner.get_run_live_calls(run_id)

    return calls


@router.get("/runs/{run_id}/summary")
def get_run_summary(run_id: str) -> dict[str, Any]:
    """Retrieve deterministic plain-English decision timeline and key findings for a run."""
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    calls = get_llm_calls(run_id)
    if not calls:
        calls = runner.get_run_live_calls(run_id)

    rec = run.get("recommendation")
    order = run.get("order")
    try:
        return summarize_run(run, calls, rec, order)
    except Exception as e:
        logger.error("Failed to generate summary for run %s: %s", run_id, e, exc_info=True)
        return {
            "overall": f"Analysis for {run.get('ticker', 'STOCK')}",
            "confidence": "Neutral",
            "steps": [],
        }


@router.post("/runs")
def trigger_run(req: RunTriggerRequest) -> dict[str, Any]:
    """Trigger an immediate analysis run for a single ticker or the entire active watchlist."""
    if req.all_watchlist:
        results = runner.start_watchlist_analysis(trigger="manual")
        return {"batch": True, "results": results}

    if not req.ticker:
        raise HTTPException(status_code=400, detail="Must provide 'ticker' or set 'all_watchlist': true")

    run_id, success, message = runner.start_analysis(
        ticker=req.ticker,
        trade_date=req.trade_date,
        trigger="manual",
        from_log_path=req.from_log_path,
    )
    if not success:
        raise HTTPException(status_code=409, detail=message)

    return {
        "run_id": run_id,
        "ticker": req.ticker.strip().upper(),
        "status": "started",
        "message": message,
    }


# ---------------------------------------------------------------------------
# Recommendations & Orders
# ---------------------------------------------------------------------------

@router.get("/recommendations")
def list_recommendations(
    limit: int = Query(default=50, ge=1, le=200),
    ticker: str | None = None,
) -> list[dict[str, Any]]:
    return get_recommendations(limit=limit, ticker=ticker)


@router.get("/orders")
def list_orders(
    limit: int = Query(default=50, ge=1, le=200),
    ticker: str | None = None,
) -> list[dict[str, Any]]:
    return get_orders(limit=limit, ticker=ticker)


@router.post("/runs/{run_id}/execute")
@router.post("/runs/{run_id}/execute-recommendation")
def execute_run_recommendation(
    run_id: str,
    req: ExecuteRecommendationRequest | None = None,
) -> dict[str, Any]:
    """Submit the bracket/limit order for a run's recommendation to Alpaca."""
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")

    rec = run.get("recommendation")
    if not rec:
        rec = get_recommendation_by_run(run_id)
    if not rec:
        raise HTTPException(status_code=400, detail="No recommendation found for this run")

    # Prevent duplicate submission if an order was already successfully submitted
    reexecute = bool(req and req.reexecute)
    ord_info = run.get("order") or get_order_by_run(run_id)
    if not reexecute and ord_info and ord_info.get("status") == "submitted":
        alp_id = ord_info.get("alpaca_order_id") or "N/A"
        raise HTTPException(
            status_code=400,
            detail=f"Order has already been submitted for this run (Alpaca Order ID: {alp_id})",
        )

    overrides = req.model_dump(exclude_none=True) if req else {}
    try:
        result = execute_recommendation(recommendation=rec, run_id=run_id, overrides=overrides)
        return result
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        logger.error("Failed to execute recommendation for run %s: %s", run_id, exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Alpaca order execution failed: {exc}")


@router.post("/recommendations/{rec_id}/execute")
@router.post("/recommendations/{rec_id}/execute-recommendation")
def execute_recommendation_by_id(
    rec_id: int,
    req: ExecuteRecommendationRequest | None = None,
) -> dict[str, Any]:
    """Submit the bracket/limit order to Alpaca by recommendation ID."""
    rec = get_recommendation(rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"Recommendation '{rec_id}' not found")

    reexecute = bool(req and req.reexecute)
    run_id = rec.get("run_id")
    if not reexecute and run_id:
        ord_info = get_order_by_run(run_id)
        if ord_info and ord_info.get("status") == "submitted":
            alp_id = ord_info.get("alpaca_order_id") or "N/A"
            raise HTTPException(
                status_code=400,
                detail=f"Order has already been submitted for this run (Alpaca Order ID: {alp_id})",
            )

    overrides = req.model_dump(exclude_none=True) if req else {}
    try:
        result = execute_recommendation(recommendation=rec, run_id=run_id, overrides=overrides)
        return result
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        logger.error("Failed to execute recommendation %s: %s", rec_id, exc, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Alpaca order execution failed: {exc}")


@router.post("/runs/{run_id}/cancel")
def cancel_run_order(run_id: str) -> dict[str, Any]:
    """Cancel a pending (submitted/open) order for a run."""
    run = get_run(run_id)
    order = run.get("order") if run else None
    if not order:
        order = get_order_by_run(run_id)

    if not order or order.get("status") != "submitted" or not order.get("alpaca_order_id"):
        raise HTTPException(
            status_code=400,
            detail="No cancelable (open) order for this run",
        )

    alpaca_order_id = str(order["alpaca_order_id"])

    # Verify live order state via Alpaca
    try:
        live_order = get_live_order(alpaca_order_id)
    except Exception as exc:
        logger.error("Failed to fetch live order %s from Alpaca: %s", alpaca_order_id, exc)
        raise HTTPException(status_code=502, detail=f"Alpaca service error: {exc}")

    terminal_statuses = {"filled", "cancelled", "canceled", "expired", "done", "rejected"}
    if live_order is None or (live_order.get("status") or "").lower() in terminal_statuses:
        raise HTTPException(
            status_code=400,
            detail="Order is no longer open; nothing to cancel.",
        )

    # Cancel on Alpaca
    try:
        cancel_live_order(alpaca_order_id)
    except Exception as exc:
        logger.error("Failed to cancel live order %s on Alpaca: %s", alpaca_order_id, exc)
        raise HTTPException(status_code=502, detail=f"Alpaca order cancellation failed: {exc}")

    # Update DB order status
    try:
        update_order_status(run_id, "cancelled")
    except Exception as e:
        logger.warning("Could not update order status for run %s: %s", run_id, e)

    # Append run log
    try:
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        append_run_log(run_id, f"[{now_str}] 🚫 Order cancelled on Alpaca (Order ID: {alpaca_order_id})\n")
    except Exception as e:
        logger.warning("Could not append log for cancelled order on run %s: %s", run_id, e)

    return {
        "success": True,
        "run_id": run_id,
        "alpaca_order_id": alpaca_order_id,
        "status": "cancelled",
    }


# ---------------------------------------------------------------------------
# Alpaca Live Active Orders
# ---------------------------------------------------------------------------

@router.get("/orders/live")
def list_live_orders() -> list[dict[str, Any]]:
    """Fetch open/pending orders directly from Alpaca."""
    try:
        return get_live_orders()
    except Exception as e:
        logger.error("Failed to fetch live orders from Alpaca: %s", e)
        raise HTTPException(status_code=503, detail=f"Alpaca service unavailable: {e}")


@router.get("/orders/live/{order_id}")
def get_single_live_order(order_id: str) -> dict[str, Any]:
    """Retrieve a single live order from Alpaca by order ID."""
    try:
        order = get_live_order(order_id)
        if not order:
            raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
        return order
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to fetch live order %s from Alpaca: %s", order_id, e)
        raise HTTPException(status_code=503, detail=f"Alpaca service unavailable: {e}")


@router.post("/orders/live/{order_id}/cancel")
def cancel_live_order_post(order_id: str) -> dict[str, Any]:
    """Cancel an active order on Alpaca."""
    try:
        cancel_live_order(order_id)
        return {"success": True, "order_id": order_id, "message": "Order cancellation submitted"}
    except Exception as e:
        logger.error("Failed to cancel live order %s on Alpaca: %s", order_id, e)
        raise HTTPException(status_code=503, detail=f"Failed to cancel order: {e}")


@router.delete("/orders/live/{order_id}")
def cancel_live_order_delete(order_id: str) -> dict[str, Any]:
    """Cancel an active order on Alpaca (DELETE alias)."""
    return cancel_live_order_post(order_id)
