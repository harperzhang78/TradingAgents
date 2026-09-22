"""Tests for cancelling submitted orders and fetching stock quotes with daily change %."""

from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient

from webapp.app import app
from webapp import db
from webapp.execution import get_stock_quote

client = TestClient(app)


# ---------------------------------------------------------------------------
# Feature 1: Cancel Pending/Submitted Order Tests
# ---------------------------------------------------------------------------

def test_cancel_submitted_open_order_success(monkeypatch):
    """A submitted order with an open live order on Alpaca cancels successfully and updates DB."""
    run_id = "run-cancel-success"
    db.create_run(run_id, "AAPL", "2026-09-21")
    rec_id = db.create_recommendation(
        run_id, "AAPL", "2026-09-21", "BUY", "BUY",
        150.0, 140.0, 170.0, "5%", "Test buy",
    )
    db.create_order_record(
        run_id, rec_id, "AAPL", "buy", "limit", 10,
        None, 150.0, 140.0, 170.0, "submitted",
        alpaca_order_id="alp-order-999",
    )

    cancelled_ids = []

    def mock_get_live_order(order_id):
        assert order_id == "alp-order-999"
        return {"id": order_id, "status": "new", "symbol": "AAPL"}

    def mock_cancel_live_order(order_id):
        cancelled_ids.append(order_id)
        return True

    monkeypatch.setattr("webapp.api.get_live_order", mock_get_live_order)
    monkeypatch.setattr("webapp.api.cancel_live_order", mock_cancel_live_order)

    res = client.post(f"/api/runs/{run_id}/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["run_id"] == run_id
    assert data["alpaca_order_id"] == "alp-order-999"
    assert data["status"] == "cancelled"

    assert cancelled_ids == ["alp-order-999"]

    # Verify DB order row was updated to cancelled
    order = db.get_order_by_run(run_id)
    assert order is not None
    assert order["status"] == "cancelled"

    # Verify run log was appended
    run = db.get_run(run_id)
    assert "🚫 Order cancelled on Alpaca (Order ID: alp-order-999)" in run["log_output"]


@pytest.mark.parametrize("terminal_status", ["filled", "cancelled", "canceled", "expired", "done", "rejected"])
def test_cancel_order_not_open_returns_400(monkeypatch, terminal_status):
    """If Alpaca reports the order is in a terminal non-open state, return 400."""
    run_id = f"run-terminal-{terminal_status}"
    db.create_run(run_id, "MSFT", "2026-09-21")
    db.create_order_record(
        run_id, None, "MSFT", "buy", "limit", 5,
        None, 400.0, 380.0, 440.0, "submitted",
        alpaca_order_id="alp-terminal-1",
    )

    monkeypatch.setattr(
        "webapp.api.get_live_order",
        lambda order_id: {"id": order_id, "status": terminal_status},
    )

    res = client.post(f"/api/runs/{run_id}/cancel")
    assert res.status_code == 400
    assert "Order is no longer open; nothing to cancel." in res.json()["detail"]

    # DB status remains submitted
    assert db.get_order_by_run(run_id)["status"] == "submitted"


def test_cancel_order_none_live_order_returns_400(monkeypatch):
    """If live order does not exist on Alpaca (get_live_order returns None), return 400."""
    run_id = "run-not-found-on-alpaca"
    db.create_run(run_id, "TSLA", "2026-09-21")
    db.create_order_record(
        run_id, None, "TSLA", "buy", "limit", 2,
        None, 200.0, None, None, "submitted",
        alpaca_order_id="alp-nonexistent",
    )

    monkeypatch.setattr("webapp.api.get_live_order", lambda order_id: None)

    res = client.post(f"/api/runs/{run_id}/cancel")
    assert res.status_code == 400
    assert "Order is no longer open" in res.json()["detail"]


def test_cancel_no_order_returns_400():
    """Attempting to cancel a run with no order record returns 400."""
    run_id = "run-without-order"
    db.create_run(run_id, "GOOG", "2026-09-21")

    res = client.post(f"/api/runs/{run_id}/cancel")
    assert res.status_code == 400
    assert "No cancelable (open) order for this run" in res.json()["detail"]


@pytest.mark.parametrize("non_submitted_status", ["skipped", "failed", "cancelled"])
def test_cancel_order_not_submitted_returns_400(non_submitted_status):
    """An order that is not in 'submitted' status cannot be cancelled."""
    run_id = f"run-status-{non_submitted_status}"
    db.create_run(run_id, "NVDA", "2026-09-21")
    db.create_order_record(
        run_id, None, "NVDA", "buy", "market", 1,
        None, None, None, None, non_submitted_status,
        alpaca_order_id="alp-ord-x",
    )

    res = client.post(f"/api/runs/{run_id}/cancel")
    assert res.status_code == 400
    assert "No cancelable (open) order for this run" in res.json()["detail"]


def test_cancel_alpaca_api_error_returns_502(monkeypatch):
    """If Alpaca cancellation throws an exception, wrap in HTTP 502."""
    run_id = "run-alpaca-error"
    db.create_run(run_id, "AMZN", "2026-09-21")
    db.create_order_record(
        run_id, None, "AMZN", "buy", "limit", 5,
        None, 180.0, 160.0, 200.0, "submitted",
        alpaca_order_id="alp-error-id",
    )

    monkeypatch.setattr(
        "webapp.api.get_live_order",
        lambda order_id: {"id": order_id, "status": "new"},
    )

    def mock_fail_cancel(order_id):
        raise RuntimeError("Alpaca network timeout")

    monkeypatch.setattr("webapp.api.cancel_live_order", mock_fail_cancel)

    res = client.post(f"/api/runs/{run_id}/cancel")
    assert res.status_code == 502
    assert "Alpaca order cancellation failed" in res.json()["detail"]


# ---------------------------------------------------------------------------
# Feature 2: Stock Quotes & Market Data Tests
# ---------------------------------------------------------------------------

def test_get_stock_quote_two_bars():
    """Mocked Alpaca client returning 2 bars returns correct current_price, prev_close, change_pct."""
    mock_bar1 = MagicMock()
    mock_bar1.close = 100.0
    mock_bar2 = MagicMock()
    mock_bar2.close = 110.0

    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = {"AAPL": [mock_bar1, mock_bar2]}
    mock_client.get_stock_latest_trade.return_value = None

    quote = get_stock_quote("AAPL", client=mock_client)
    assert quote is not None
    assert quote["symbol"] == "AAPL"
    assert quote["current_price"] == 110.0
    assert quote["prev_close"] == 100.0
    assert quote["change_pct"] == pytest.approx(10.0)


def test_get_stock_quote_one_bar():
    """Mocked Alpaca client returning 1 bar returns current_price with prev_close & change_pct as None."""
    mock_bar1 = MagicMock()
    mock_bar1.close = 150.0

    mock_client = MagicMock()
    mock_client.get_stock_bars.return_value = {"MSFT": [mock_bar1]}
    mock_client.get_stock_latest_trade.return_value = None

    quote = get_stock_quote("MSFT", client=mock_client)
    assert quote is not None
    assert quote["symbol"] == "MSFT"
    assert quote["current_price"] == 150.0
    assert quote["prev_close"] is None
    assert quote["change_pct"] is None


def test_get_stock_quote_api_error():
    """Mocked Alpaca client raising an exception returns None without raising."""
    mock_client = MagicMock()
    mock_client.get_stock_bars.side_effect = RuntimeError("Alpaca data rate limit")

    quote = get_stock_quote("NVDA", client=mock_client)
    assert quote is None


def test_get_stock_quotes_api_with_symbols(monkeypatch):
    """GET /api/stocks/quotes?symbols=AAPL,MSFT returns mapped quotes for requested tickers."""
    def mock_quote(sym, client=None):
        if sym == "AAPL":
            return {"symbol": "AAPL", "current_price": 220.0, "prev_close": 215.0, "change_pct": 2.3256}
        elif sym == "MSFT":
            return {"symbol": "MSFT", "current_price": 450.0, "prev_close": 460.0, "change_pct": -2.1739}
        return None

    monkeypatch.setattr("webapp.api.get_stock_quote", mock_quote)

    res = client.get("/api/stocks/quotes?symbols=AAPL,MSFT")
    assert res.status_code == 200
    data = res.json()
    assert "AAPL" in data
    assert data["AAPL"]["current_price"] == 220.0
    assert data["AAPL"]["change_pct"] == 2.3256
    assert "MSFT" in data
    assert data["MSFT"]["change_pct"] == -2.1739


def test_get_stock_quotes_api_default_watchlist(monkeypatch):
    """GET /api/stocks/quotes without symbols defaults to current watchlist symbols."""
    db.add_watchlist_item("GOOG", "Alphabet")
    db.add_watchlist_item("AMZN", "Amazon")

    called_syms = []

    def mock_quote(sym, client=None):
        called_syms.append(sym)
        return {"symbol": sym, "current_price": 100.0, "prev_close": 98.0, "change_pct": 2.04}

    monkeypatch.setattr("webapp.api.get_stock_quote", mock_quote)

    res = client.get("/api/stocks/quotes")
    assert res.status_code == 200
    data = res.json()
    assert "GOOG" in data
    assert "AMZN" in data
    assert "GOOG" in called_syms
    assert "AMZN" in called_syms


def test_get_stock_quotes_api_resilient_on_error(monkeypatch):
    """GET /api/stocks/quotes handles errors without crashing (returns empty dict)."""
    def mock_fail(sym, client=None):
        raise RuntimeError("Data lookup failed")

    monkeypatch.setattr("webapp.api.get_stock_quote", mock_fail)

    res = client.get("/api/stocks/quotes?symbols=FAIL")
    assert res.status_code == 200
    assert res.json() == {}


# ---------------------------------------------------------------------------
# UI & Static Assets Integration Tests
# ---------------------------------------------------------------------------

def test_cache_busting_and_ui_elements():
    """Verify ?v=20260921_v6 is used in both HTML files and new columns/classes exist."""
    # Check templates/index.html and static/index.html
    for path in ["webapp/templates/index.html", "webapp/static/index.html"]:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert ("style.css?v=20260921_v7" in content or "style.css?v=20260921_v6" in content), f"Missing cache bust in {path}"
        assert ("app.js?v=20260921_v7" in content or "app.js?v=20260921_v6" in content), f"Missing script cache bust in {path}"
        assert "<th>Price</th>" in content, f"Missing Price header in {path}"
        assert "<th>Chg %</th>" in content, f"Missing Chg % header in {path}"
        assert '<td colspan="8"' in content, f"Colspan not updated to 8 in {path}"

    # Check style.css
    with open("webapp/static/style.css", "r", encoding="utf-8") as f:
        css = f.read()
    assert ".chip-change" in css
    assert ".chip-change-up" in css
    assert ".chip-change-down" in css

    # Check app.js
    with open("webapp/static/app.js", "r", encoding="utf-8") as f:
        js = f.read()
    assert "stockQuotes:" in js
    assert "loadStockQuotes" in js
    assert "cancelRunOrder" in js
    assert "window.cancelRunOrder = cancelRunOrder;" in js
    assert "window.loadStockQuotes = loadStockQuotes;" in js
    assert "chip-change" in js
    assert "✕ Cancel Order" in js
