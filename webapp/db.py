"""SQLite database management and operations for the TradingAgents Web App."""
from __future__ import annotations

import contextlib
import json
import socket
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Generator

import os
from pathlib import Path

from webapp.config import (
    DATABASE_PATH,
    DEFAULT_AUTO_TRADE,
    DEFAULT_PORT,
    DEFAULT_SCHEDULE_ENABLED,
    DEFAULT_SCHEDULE_INTERVAL_MINUTES,
    DEFAULT_WATCHLIST,
    get_database_path,
)


def get_db_path() -> Path | str:
    """Return the active database path, allowing dynamic override via env or module attribute."""
    override = os.environ.get("TRADING_DB_PATH")
    if override:
        return override
    import webapp.db as db_mod
    if hasattr(db_mod, "DATABASE_PATH") and db_mod.DATABASE_PATH is not None:
        return db_mod.DATABASE_PATH
    return get_database_path()


@contextlib.contextmanager
def get_db(db_path: Path | str | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Provide a transactional scope around a series of operations."""
    target_path = db_path if db_path is not None else get_db_path()
    target_str = str(target_path)
    is_uri = target_str.startswith("file:")
    if not is_uri and target_str != ":memory:":
        Path(target_str).parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(target_str, timeout=30.0, uri=is_uri)
    conn.row_factory = sqlite3.Row
    if target_str != ":memory:" and not is_uri:
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


def _server_port() -> int:
    """Read the server port without parsing unrelated CLI arguments."""
    for index, arg in enumerate(sys.argv[1:], start=1):
        if arg == "--port" or arg.startswith("--port="):
            try:
                value = sys.argv[index + 1] if arg == "--port" else arg.split("=", 1)[1]
                port = int(value)
                if 0 <= port <= 65535:
                    return port
            except (IndexError, ValueError):
                pass
            return DEFAULT_PORT
    return DEFAULT_PORT


def _server_port_available() -> bool:
    """Only allow cleanup when the server port can definitely be bound."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", _server_port()))
    except OSError:
        # An occupied port (or an inconclusive probe) must not interrupt live runs.
        return False
    return True


def init_db(db_path: Path | str | None = None) -> None:
    """Initialize database tables and default configuration."""
    with get_db(db_path) as conn:
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

        CREATE TABLE IF NOT EXISTS llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            node TEXT,
            agent TEXT,
            kind TEXT,
            model TEXT,
            tier TEXT,
            tool_name TEXT,
            request TEXT,
            response TEXT,
            ok INTEGER NOT NULL DEFAULT 1,
            latency_ms INTEGER DEFAULT 0,
            error TEXT,
            ts TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_llm_calls_run_seq ON llm_calls(run_id, seq);
        """)

        # Initialize default settings if missing
        cur = conn.cursor()
        defaults = {
            "auto_trade": "true" if DEFAULT_AUTO_TRADE else "false",
            "schedule_enabled": "true" if DEFAULT_SCHEDULE_ENABLED else "false",
            "schedule_interval_minutes": str(DEFAULT_SCHEDULE_INTERVAL_MINUTES),
            "last_scheduled_run": "",
            "lang": "en",
            "output_language": "English",
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

        # Uvicorn runs lifespan initialization before binding its listening socket.
        # A second instance that cannot bind must not interrupt the serving instance.
        if _server_port_available():
            now_iso = datetime.now(timezone.utc).isoformat()
            cur.execute(
                """
                UPDATE runs
                SET status = 'failed',
                    completed_at = ?,
                    error = 'Run interrupted: server restarted while execution was in progress'
                WHERE status = 'running'
                """,
                (now_iso,),
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


def remove_watchlist_items(symbols: list[str]) -> int:
    clean_syms = [s.strip().upper() for s in symbols if isinstance(s, str) and s.strip()]
    if not clean_syms:
        return 0
    with get_db() as conn:
        placeholders = ",".join("?" for _ in clean_syms)
        cur = conn.execute(f"DELETE FROM watchlist WHERE symbol IN ({placeholders})", clean_syms)
        return cur.rowcount



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


def delete_run(run_id: str) -> bool:
    """Delete a run and its children through the enabled foreign-key cascades."""
    with get_db() as conn:
        return conn.execute("DELETE FROM runs WHERE id = ?", (run_id,)).rowcount > 0


def has_submitted_order(run_id: str) -> bool:
    with get_db() as conn:
        return conn.execute(
            "SELECT 1 FROM orders WHERE run_id = ? AND status = 'submitted' LIMIT 1",
            (run_id,),
        ).fetchone() is not None


def get_run(run_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        rec = conn.execute("SELECT * FROM recommendations WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
        data["recommendation"] = dict(rec) if rec else None
        ord_row = conn.execute("SELECT * FROM orders WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
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
            rec = conn.execute("SELECT * FROM recommendations WHERE run_id = ? ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
            d["recommendation"] = dict(rec) if rec else None
            ord_row = conn.execute("SELECT * FROM orders WHERE run_id = ? ORDER BY id DESC LIMIT 1", (d["id"],)).fetchone()
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


def get_recommendation(recommendation_id: int) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM recommendations WHERE id = ?",
            (recommendation_id,),
        ).fetchone()
        return dict(row) if row else None


def get_recommendation_by_run(run_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM recommendations WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None


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


def get_order_by_run(run_id: str) -> dict[str, Any] | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None


def record_order_execution(
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
    """Record or update an order execution in the database."""
    now_str = datetime.now(timezone.utc).isoformat()
    raw_str = json.dumps(raw_response, default=str) if raw_response else None
    with get_db() as conn:
        # Check if an existing order record for this run was skipped or failed
        existing = conn.execute(
            "SELECT id FROM orders WHERE run_id = ? AND status IN ('skipped', 'failed') ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()

        if existing:
            order_id = existing["id"]
            conn.execute(
                """
                UPDATE orders SET
                    recommendation_id = COALESCE(?, recommendation_id),
                    ticker = ?,
                    side = ?,
                    order_type = ?,
                    qty = ?,
                    notional = ?,
                    limit_price = ?,
                    stop_price = ?,
                    take_profit_price = ?,
                    status = ?,
                    alpaca_order_id = ?,
                    client_order_id = ?,
                    skip_reason = ?,
                    error_message = ?,
                    submitted_at = ?,
                    raw_response = ?
                WHERE id = ?
                """,
                (
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
                    order_id,
                ),
            )
            return order_id
        else:
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


def update_order_status(run_id_or_id: int | str, status: str) -> None:
    """Update order status by order ID or run ID."""
    with get_db() as conn:
        if isinstance(run_id_or_id, int) or (isinstance(run_id_or_id, str) and run_id_or_id.isdigit()):
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, int(run_id_or_id)))
        else:
            conn.execute("UPDATE orders SET status = ? WHERE run_id = ?", (status, str(run_id_or_id)))


# ---------------------------------------------------------------------------
# LLM Calls Queries
# ---------------------------------------------------------------------------

def insert_llm_calls(run_id: str, calls: list[dict[str, Any]]) -> None:
    """Batch insert captured LLM and tool calls for a run."""
    if not calls:
        return
    with get_db() as conn:
        for c in calls:
            req_raw = c.get("request")
            if isinstance(req_raw, (dict, list)):
                req_str = json.dumps(req_raw, default=str)
            else:
                req_str = str(req_raw) if req_raw is not None else None

            resp_raw = c.get("response")
            if isinstance(resp_raw, (dict, list)):
                resp_str = json.dumps(resp_raw, default=str)
            else:
                resp_str = str(resp_raw) if resp_raw is not None else None

            conn.execute(
                """
                INSERT INTO llm_calls (
                    run_id, seq, node, agent, kind, model, tier,
                    tool_name, request, response, ok, latency_ms, error, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    c.get("seq", 0),
                    c.get("node"),
                    c.get("agent"),
                    c.get("kind", "llm"),
                    c.get("model"),
                    c.get("tier"),
                    c.get("tool_name"),
                    req_str,
                    resp_str,
                    1 if c.get("ok", True) else 0,
                    c.get("latency_ms", 0),
                    c.get("error"),
                    c.get("ts"),
                ),
            )


def get_llm_calls(run_id: str) -> list[dict[str, Any]]:
    """Retrieve all LLM and tool calls for a run, ordered by sequence number."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY seq ASC",
            (run_id,),
        ).fetchall()
        results = []
        for r in rows:
            d = dict(r)
            d["ok"] = bool(d["ok"])

            if d.get("request") and (d["request"].startswith("{") or d["request"].startswith("[")):
                try:
                    d["request"] = json.loads(d["request"])
                except Exception:
                    pass

            if d.get("response") and (d["response"].startswith("{") or d["response"].startswith("[")):
                try:
                    d["response"] = json.loads(d["response"])
                except Exception:
                    pass

            results.append(d)
        return results
