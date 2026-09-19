"""FastAPI router defining REST endpoints for the TradingAgents Dashboard."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from webapp.db import (
    add_watchlist_item,
    get_all_settings,
    get_orders,
    get_recommendations,
    get_run,
    get_runs,
    get_watchlist,
    remove_watchlist_item,
    set_setting,
    update_watchlist_item,
)
from webapp.execution import get_account_overview
from webapp.runner import runner

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


class SettingsUpdateRequest(BaseModel):
    auto_trade: bool | None = None
    schedule_enabled: bool | None = None
    schedule_interval_minutes: int | None = None


class RunTriggerRequest(BaseModel):
    ticker: str | None = None
    all_watchlist: bool = False
    trade_date: str | None = None
    from_log_path: str | None = None


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


@router.get("/runs/{run_id}")
def get_run_detail(run_id: str) -> dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    # Merge live memory logs if still running
    mem_log = runner.get_run_memory_logs(run_id)
    if mem_log and len(mem_log) > len(run.get("log_output", "")):
        run["log_output"] = mem_log
    return run


@router.get("/runs/{run_id}/logs")
def get_run_logs(run_id: str) -> dict[str, str]:
    run = get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    logs = run.get("log_output", "")
    mem_log = runner.get_run_memory_logs(run_id)
    if len(mem_log) > len(logs):
        logs = mem_log
    return {"logs": logs}


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
