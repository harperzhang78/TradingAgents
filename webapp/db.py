"""SQLite database management and operations for the TradingAgents Web App."""
from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Generator

from webapp.config import (
    DATABASE_PATH,
    DEFAULT_AUTO_TRADE,
    DEFAULT_SCHEDULE_ENABLED,
    DEFAULT_SCHEDULE_INTERVAL_MINUTES,
    DEFAULT_WATCHLIST,
)


@contextlib.contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional scope around a series of operations."""
    conn = sqlite3.connect(str(DATABASE_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database tables and default configuration."""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            added_at TEXT NOT NULL,
            notes TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            trigger TEXT NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            error TEXT,
            log_output TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            ticker TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            created_at TEXT NOT NULL,
            action TEXT,
            rating TEXT,
            entry_price REAL,
            stop_loss REAL,
            price_target REAL,
            position_sizing TEXT,
            reasoning TEXT,
            current_position_qty REAL,
            current_position_avg_price REAL,
            final_trade_decision TEXT,
            trader_investment_plan TEXT
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            recommendation_id INTEGER REFERENCES recommendations(id) ON DELETE SET NULL,
            ticker TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            qty INTEGER,
            notional REAL,
            limit_price REAL,
            stop_price REAL,
            take_profit_price REAL,
            status TEXT NOT NULL,
            alpaca_order_id TEXT,
            client_order_id TEXT,
            skip_reason TEXT,
            error_message TEXT,
            submitted_at TEXT NOT NULL,
            raw_response TEXT
        );
        """)

        # Initialize default settings if missing
        cur = conn.cursor()
        defaults = {
            "auto_trade": "true" if DEFAULT_AUTO_TRADE else "false",
            "schedule_enabled": "true" if DEFAULT_SCHEDULE_ENABLED else "false",
            "schedule_interval_minutes": str(DEFAULT_SCHEDULE_INTERVAL_MINUTES),
            "last_scheduled_run": "",
        }
        for k, v in defaults.items():
            cur.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

        # Initialize default watchlist if table is empty
        cur.execute("SELECT COUNT(*) FROM watchlist")
        if cur.fetchone()[0] == 0:
            now_str = datetime.now(timezone.utc).isoformat()
            for symbol in DEFAULT_WATCHLIST:
                cur.execute(
                    "INSERT OR IGNORE INTO watchlist (symbol, enabled, added_at, notes) VALUES (?, 1, ?, ?)",
                    (symbol.upper(), now_str, "Default watchlist entry"),
                )


# ---------------------------------------------------------------------------
# Watchlist Queries
# ---------------------------------------------------------------------------

def get_watchlist() -> list[dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM watchlist ORDER BY symbol ASC").fetchall()
        return [dict(r) for r in rows]


def get_active_watchlist() -> list[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT symbol FROM watchlist WHERE enabled = 1 ORDER BY symbol ASC").fetchall()
        return [r["symbol"] for r in rows]


def add_watchlist_item(symbol: str, notes: str = "") -> dict[str, Any]:
    clean_sym = symbol.strip().upper()
    if not clean_sym:
        raise ValueError("Symbol cannot be empty")
    now_str = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO watchlist (symbol, enabled, added_at, notes) VALUES (?, 1, ?, ?) "
            "ON CONFLICT(symbol) DO UPDATE SET enabled = 1, notes = excluded.notes",
            (clean_sym, now_str, notes),
        )
        row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (clean_sym,)).fetchone()
        return dict(row)


def remove_watchlist_item(symbol: str) -> bool:
    clean_sym = symbol.strip().upper()
    with get_db() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (clean_sym,))
        return cur.rowcount > 0


def update_watchlist_item(symbol: str, enabled: bool | None = None, notes: str | None = None) -> dict[str, Any] | None:
    clean_sym = symbol.strip().upper()
    with get_db() as conn:
        row = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (clean_sym,)).fetchone()
        if not row:
            return None
        new_enabled = int(enabled) if enabled is not None else row["enabled"]
        new_notes = notes if notes is not None else row["notes"]
        conn.execute(
            "UPDATE watchlist SET enabled = ?, notes = ? WHERE symbol = ?",
            (new_enabled, new_notes, clean_sym),
        )
        updated = conn.execute("SELECT * FROM watchlist WHERE symbol = ?", (clean_sym,)).fetchone()
        return dict(updated)


# ---------------------------------------------------------------------------
# Settings Queries
# ---------------------------------------------------------------------------

def get_all_settings() -> dict[str, str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}


def get_setting(key: str, default: str = "") -> str:
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def is_auto_trade_enabled() -> bool:
    return get_setting("auto_trade", "false").strip().lower() in ("true", "1", "yes", "on")


# ---------------------------------------------------------------------------
# Runs Queries
# ---------------------------------------------------------------------------

def create_run(run_id: str, ticker: str, trade_date: str, trigger: str = "manual") -> dict[str, Any]:
    clean_ticker = ticker.strip().upper()
    now_str = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO runs (id, ticker, trade_date, trigger, status, started_at, log_output) "
            "VALUES (?, ?, ?, ?, 'running', ?, '')",
            (run_id, clean_ticker, trade_date, trigger, now_str),
        )
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row)


def update_run_status(
    run_id: str,
    status: str,
    completed_at: str | None = None,
    error: str | None = None,
) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE runs SET status = ?, completed_at = ?, error = ? WHERE id = ?",
            (status, completed_at, error, run_id),
        )


def append_run_log(run_id: str, text: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE runs SET log_output = log_output || ? WHERE id = ?",
            (text, run_id),
        )


def get_run(run_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        rec = conn.execute("SELECT * FROM recommendations WHERE run_id = ?", (run_id,)).fetchone()
        data["recommendation"] = dict(rec) if rec else None
        ord_row = conn.execute("SELECT * FROM orders WHERE run_id = ?", (run_id,)).fetchone()
        data["order"] = dict(ord_row) if ord_row else None
        return data


def get_runs(limit: int = 50, ticker: str | None = None) -> list[dict[str, Any]]:
    with get_db() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM runs WHERE ticker = ? ORDER BY started_at DESC LIMIT ?",
                (ticker.strip().upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            rec = conn.execute("SELECT * FROM recommendations WHERE run_id = ?", (d["id"],)).fetchone()
            d["recommendation"] = dict(rec) if rec else None
            ord_row = conn.execute("SELECT * FROM orders WHERE run_id = ?", (d["id"],)).fetchone()
            d["order"] = dict(ord_row) if ord_row else None
            results.append(d)
        return results


# ---------------------------------------------------------------------------
# Recommendations Queries
# ---------------------------------------------------------------------------

def create_recommendation(
    run_id: str,
    ticker: str,
    trade_date: str,
    action: str | None,
    rating: str | None,
    entry_price: float | None,
    stop_loss: float | None,
    price_target: float | None,
    position_sizing: str | None,
    reasoning: str | None,
    current_position_qty: float | None = None,
    current_position_avg_price: float | None = None,
    final_trade_decision: str | None = None,
    trader_investment_plan: str | None = None,
) -> int:
    now_str = datetime.now(timezone.utc).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO recommendations (
                run_id, ticker, trade_date, created_at, action, rating,
                entry_price, stop_loss, price_target, position_sizing,
                reasoning, current_position_qty, current_position_avg_price,
                final_trade_decision, trader_investment_plan
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                ticker.strip().upper(),
                trade_date,
                now_str,
                action,
                rating,
                entry_price,
                stop_loss,
                price_target,
                position_sizing,
                reasoning,
                current_position_qty,
                current_position_avg_price,
                final_trade_decision,
                trader_investment_plan,
            ),
        )
        return cur.lastrowid


def get_recommendations(limit: int = 50, ticker: str | None = None) -> list[dict[str, Any]]:
    with get_db() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM recommendations WHERE ticker = ? ORDER BY created_at DESC LIMIT ?",
                (ticker.strip().upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM recommendations ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Orders Queries
# ---------------------------------------------------------------------------

def create_order_record(
    run_id: str,
    recommendation_id: int | None,
    ticker: str,
    side: str,
    order_type: str,
    qty: int | None,
    notional: float | None,
    limit_price: float | None,
    stop_price: float | None,
    take_profit_price: float | None,
    status: str,
    alpaca_order_id: str | None = None,
    client_order_id: str | None = None,
    skip_reason: str | None = None,
    error_message: str | None = None,
    raw_response: dict | None = None,
) -> int:
    now_str = datetime.now(timezone.utc).isoformat()
    raw_str = json.dumps(raw_response, default=str) if raw_response else None
    with get_db() as conn:
        cur = conn.execute(
            """
            INSERT INTO orders (
                run_id, recommendation_id, ticker, side, order_type,
                qty, notional, limit_price, stop_price, take_profit_price,
                status, alpaca_order_id, client_order_id, skip_reason,
                error_message, submitted_at, raw_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                recommendation_id,
                ticker.strip().upper(),
                side,
                order_type,
                qty,
                notional,
                limit_price,
                stop_price,
                take_profit_price,
                status,
                alpaca_order_id,
                client_order_id,
                skip_reason,
                error_message,
                now_str,
                raw_str,
            ),
        )
        return cur.lastrowid


def get_orders(limit: int = 50, ticker: str | None = None) -> list[dict[str, Any]]:
    with get_db() as conn:
        if ticker:
            rows = conn.execute(
                "SELECT * FROM orders WHERE ticker = ? ORDER BY submitted_at DESC LIMIT ?",
                (ticker.strip().upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY submitted_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
