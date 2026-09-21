"""Tests for Watchlist API endpoints and batch deletion functionality."""

import pytest
from fastapi.testclient import TestClient

from webapp.app import app
from webapp.db import add_watchlist_item, get_watchlist, remove_watchlist_items

client = TestClient(app)


def test_remove_watchlist_items_db():
    """Verify db helper remove_watchlist_items removes multiple items in a single query."""
    add_watchlist_item("AAPL", "Apple Inc.")
    add_watchlist_item("NVDA", "Nvidia Corp.")
    add_watchlist_item("MSFT", "Microsoft Corp.")
    add_watchlist_item("TSLA", "Tesla Inc.")

    initial = [item["symbol"] for item in get_watchlist()]
    assert "AAPL" in initial
    assert "NVDA" in initial
    assert "MSFT" in initial
    assert "TSLA" in initial

    # Batch delete two symbols
    deleted_count = remove_watchlist_items(["AAPL", "NVDA"])
    assert deleted_count == 2

    remaining = [item["symbol"] for item in get_watchlist()]
    assert "AAPL" not in remaining
    assert "NVDA" not in remaining
    assert "MSFT" in remaining
    assert "TSLA" in remaining

    # Deleting non-existent or empty list returns 0
    assert remove_watchlist_items([]) == 0
    assert remove_watchlist_items(["NONEXISTENT"]) == 0


def test_batch_delete_watchlist_api_post():
    """Verify POST /api/watchlist/batch-delete deletes selected tickers."""
    add_watchlist_item("GOOG", "Alphabet Inc.")
    add_watchlist_item("AMZN", "Amazon.com Inc.")
    add_watchlist_item("META", "Meta Platforms")

    response = client.post(
        "/api/watchlist/batch-delete",
        json={"symbols": ["GOOG", "AMZN"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert data["count"] == 2
    assert "GOOG" in data["symbols"]
    assert "AMZN" in data["symbols"]

    # Verify via GET
    get_res = client.get("/api/watchlist")
    assert get_res.status_code == 200
    syms = [item["symbol"] for item in get_res.json()]
    assert "GOOG" not in syms
    assert "AMZN" not in syms
    assert "META" in syms


def test_batch_delete_watchlist_api_delete_method():
    """Verify DELETE /api/watchlist also supports batch deletion via HTTP DELETE."""
    add_watchlist_item("NFLX", "Netflix")
    add_watchlist_item("AMD", "Advanced Micro Devices")

    response = client.request(
        "DELETE",
        "/api/watchlist",
        json={"symbols": ["NFLX", "AMD"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] is True
    assert data["count"] == 2

    get_res = client.get("/api/watchlist")
    syms = [item["symbol"] for item in get_res.json()]
    assert "NFLX" not in syms
    assert "AMD" not in syms


def test_batch_delete_watchlist_validation_error():
    """Verify empty symbols list fails validation."""
    response = client.post(
        "/api/watchlist/batch-delete",
        json={"symbols": []},
    )
    assert response.status_code == 422


def test_single_delete_endpoint_retained():
    """Verify individual DELETE /api/watchlist/{symbol} remains supported."""
    add_watchlist_item("INTC", "Intel")
    res = client.delete("/api/watchlist/INTC")
    assert res.status_code == 200
    assert res.json() == {"deleted": True}

    res_404 = client.delete("/api/watchlist/INTC")
    assert res_404.status_code == 404


def test_watchlist_ui_elements_and_static_files():
    """Verify frontend HTML & JS contain batch delete controls and no row-level delete button."""
    index_res = client.get("/")
    assert index_res.status_code == 200
    html = index_res.text

    # Header select-all checkbox and batch delete button exist
    assert "id=\"watchlist-select-all\"" in html
    assert ("btn-watchlist-batch-delete" in html or "btn-delete-selected" in html)
    assert "Delete Selected" in html

    # Verify static assets
    js_res = client.get("/static/app.js")
    assert js_res.status_code == 200
    js = js_res.text

    assert "deleteSelectedWatchlist" in js
    assert "batchDeleteWatchlist" in js
    assert "toggleWatchlistSelectAll" in js
    assert "onWatchlistSelectChange" in js
    assert "watchlist-row-select" in js

    # Verify individual delete button beside Run button is removed from row template
    assert "removeWatchlistSymbol('${sym}')" not in js

