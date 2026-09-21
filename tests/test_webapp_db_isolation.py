"""Tests verifying that database operations in test environments are isolated from the live database."""

import os
import sqlite3
from pathlib import Path

from webapp.config import DATA_DIR
from webapp.db import create_run, get_db, get_db_path, get_runs, init_db


def test_test_database_is_isolated_from_production():
    """Verify that tests never connect to the live production database."""
    live_db_path = DATA_DIR / "trading_dashboard.db"
    active_path = Path(get_db_path())

    # Active DB path during testing must not be the production DB path
    assert active_path != live_db_path
    assert "trading_dashboard.db" in str(active_path) or "test_" in str(active_path)
    # TRADING_DB_PATH must be set in the environment
    assert os.environ.get("TRADING_DB_PATH") is not None


def test_db_writes_do_not_mutate_live_database(tmp_path):
    """Verify that creating runs and records only affects the isolated test DB."""
    live_db_path = DATA_DIR / "trading_dashboard.db"
    live_mtime_before = os.path.getmtime(live_db_path) if live_db_path.exists() else None

    # Write a test run into whatever active test DB is configured
    test_run_id = "isolation-test-run-id-999"
    created = create_run(test_run_id, "TESTTICKER", "2026-09-20", "manual")
    assert created["id"] == test_run_id

    # Verify the test run exists in the active test DB
    runs = get_runs(limit=10, ticker="TESTTICKER")
    assert any(r["id"] == test_run_id for r in runs)

    # Verify the live production database was NOT touched
    if live_db_path.exists():
        assert os.path.getmtime(live_db_path) == live_mtime_before
        conn = sqlite3.connect(str(live_db_path))
        found = conn.execute("SELECT COUNT(*) FROM runs WHERE id = ?", (test_run_id,)).fetchone()[0]
        conn.close()
        assert found == 0, "Test run was found in production database!"


def test_custom_trading_db_path_override(tmp_path, monkeypatch):
    """Verify TRADING_DB_PATH can explicitly redirect to a custom path."""
    custom_db = tmp_path / "custom_isolated.db"
    monkeypatch.setenv("TRADING_DB_PATH", str(custom_db))

    init_db(custom_db)
    with get_db() as conn:
        conn.execute(
            "INSERT INTO watchlist (symbol, enabled, added_at, notes) VALUES (?, 1, ?, ?)",
            ("ISOTEST", "2026-09-20T00:00:00Z", "Isolation check"),
        )

    # Verify custom DB has the row
    conn = sqlite3.connect(str(custom_db))
    row = conn.execute("SELECT symbol FROM watchlist WHERE symbol = 'ISOTEST'").fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "ISOTEST"
