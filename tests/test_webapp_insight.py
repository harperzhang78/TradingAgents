"""Tests for webapp Run Details, Decision Summary, and LLM Calls endpoints."""

import uuid
from fastapi.testclient import TestClient

from webapp.app import app
from webapp.db import create_run, insert_llm_calls, update_run_status
from webapp.summary import summarize_run
from tradingagents.llm_clients.llm_trace import start_capture, stop_capture, record

client = TestClient(app)


def test_get_run_detail_not_found():
    res = client.get(f"/api/runs/{uuid.uuid4()}")
    assert res.status_code == 404
    assert res.json()["detail"] == "Run not found"


def test_get_run_logs_not_found():
    res = client.get(f"/api/runs/{uuid.uuid4()}/logs")
    assert res.status_code == 404
    assert res.json()["detail"] == "Run not found"


def test_get_run_llm_calls_not_found():
    res = client.get(f"/api/runs/{uuid.uuid4()}/llm-calls")
    assert res.status_code == 404
    assert res.json()["detail"] == "Run not found"


def test_get_run_summary_not_found():
    res = client.get(f"/api/runs/{uuid.uuid4()}/summary")
    assert res.status_code == 404
    assert res.json()["detail"] == "Run not found"


def test_run_detail_and_logs_existing():
    run_id = str(uuid.uuid4())
    create_run(run_id, "AAPL", "2026-09-20", "manual")
    update_run_status(run_id, "completed")

    detail_res = client.get(f"/api/runs/{run_id}")
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert detail["id"] == run_id
    assert detail["ticker"] == "AAPL"
    assert detail["status"] == "completed"

    logs_res = client.get(f"/api/runs/{run_id}/logs")
    assert logs_res.status_code == 200
    assert "logs" in logs_res.json()


def test_llm_calls_and_summary_persisted():
    run_id = str(uuid.uuid4())
    create_run(run_id, "NVDA", "2026-09-20", "manual")

    dummy_calls = [
        {
            "seq": 1,
            "node": "Market Analyst",
            "agent": "Market Analyst",
            "kind": "llm",
            "model": "gemini-flash",
            "tier": "quick",
            "tool_name": None,
            "request": {"messages": [{"role": "user", "content": "Analyze NVDA"}]},
            "response": "Technical indicators suggest momentum.",
            "ok": True,
            "latency_ms": 120,
            "error": None,
            "ts": "2026-09-20T10:00:00Z",
        },
        {
            "seq": 2,
            "node": "Market Analyst",
            "agent": "Market Analyst",
            "kind": "tool",
            "model": "gemini-flash",
            "tier": "quick",
            "tool_name": "get_stock_data",
            "request": {"ticker": "NVDA"},
            "response": {"close": 225.5},
            "ok": True,
            "latency_ms": 45,
            "error": None,
            "ts": "2026-09-20T10:00:01Z",
        },
        {
            "seq": 3,
            "node": "Trader",
            "agent": "Trader",
            "kind": "llm",
            "model": "gemini-pro",
            "tier": "deep",
            "tool_name": None,
            "request": {"messages": [{"role": "user", "content": "Size position"}]},
            "response": "Buy 15 shares with limit 225.5.",
            "ok": True,
            "latency_ms": 350,
            "error": None,
            "ts": "2026-09-20T10:00:02Z",
        },
    ]
    insert_llm_calls(run_id, dummy_calls)
    update_run_status(run_id, "completed")

    # Verify llm-calls endpoint
    calls_res = client.get(f"/api/runs/{run_id}/llm-calls")
    assert calls_res.status_code == 200
    calls = calls_res.json()
    assert len(calls) == 3
    assert calls[0]["seq"] == 1
    assert calls[0]["agent"] == "Market Analyst"
    assert calls[1]["kind"] == "tool"
    assert calls[1]["tool_name"] == "get_stock_data"

    # Verify summary endpoint
    sum_res = client.get(f"/api/runs/{run_id}/summary")
    assert sum_res.status_code == 200
    summary = sum_res.json()
    assert "overall" in summary
    assert "confidence" in summary
    assert "steps" in summary
    assert len(summary["steps"]) == 5
    assert summary["steps"][0]["llm_count"] == 1
    assert "get_stock_data" in summary["steps"][0]["tools"]


def test_summarize_run_edge_cases():
    # 1. Null node in calls, string prices, and string sentiment score
    run = {
        "id": "edge-1",
        "ticker": "MSFT",
        "status": "completed",
        "recommendation": {
            "action": "BUY",
            "rating": "Overweight",
            "entry_price": "410.25",
            "stop_loss": "395.00",
            "price_target": "450.00",
            "position_sizing": "4%",
            "reasoning": "Strong cloud revenue growth.",
        },
        "order": {
            "status": "submitted",
            "qty": 10,
            "order_type": "bracket",
            "limit_price": "410.25",
            "stop_price": "395.00",
            "take_profit_price": "450.00",
        },
    }
    calls = [
        {"seq": 1, "node": None, "agent": None, "kind": "llm", "response": None, "ok": False},
        {
            "seq": 2,
            "node": "Sentiment Analyst",
            "kind": "llm",
            "response": {"band": "Bullish", "score": "8.5"},
            "ok": True,
        },
        {"seq": 3, "node": "Trader", "kind": "llm", "response": "Buy 10 shares", "ok": True},
    ]

    res = summarize_run(run, calls)
    assert "BUY MSFT" in res["overall"]
    assert "10 shares" in res["overall"]
    assert res["confidence"] == "Overweight"
    assert len(res["steps"]) == 5
    assert "Sentiment: Bullish (8.5/10)" in res["steps"][0]["key_find"]

    # 2. Running state with empty calls
    run_running = {"id": "edge-2", "ticker": "TSLA", "status": "running"}
    res_running = summarize_run(run_running, [])
    assert "IN PROGRESS" in res_running["overall"]

    # 3. Failed run with error
    run_failed = {"id": "edge-3", "ticker": "GOOGL", "status": "failed", "error": "Connection reset"}
    res_failed = summarize_run(run_failed, [])
    assert "FAILED" in res_failed["overall"]
    assert "Connection reset" in res_failed["overall"]


def test_in_flight_detail_multiple_concurrent_stocks():
    from webapp.runner import runner
    from tradingagents.llm_clients.llm_trace import _active_runs_registry, _global_lock

    run_nvda = "test-run-nvda-123"
    run_aapl = "test-run-aapl-456"

    # Register two concurrent runs in runner
    with runner._lock:
        runner._active_runs[run_nvda] = {
            "ticker": "NVDA",
            "trade_date": "2026-09-20",
            "trigger": "manual",
            "started_at": "2026-09-20T12:00:00Z",
            "log_buffer": "Analyzing NVDA...",
        }
        runner._active_runs[run_aapl] = {
            "ticker": "AAPL",
            "trade_date": "2026-09-20",
            "trigger": "manual",
            "started_at": "2026-09-20T12:00:05Z",
            "log_buffer": "Analyzing AAPL...",
        }

    # Register active captured calls and current node in llm_trace
    with _global_lock:
        _active_runs_registry[run_nvda] = {
            "current_node": "Market Analyst",
            "captured": [
                {
                    "seq": 1,
                    "node": "Market Analyst",
                    "agent": "Market Analyst",
                    "kind": "llm",
                    "latency_ms": 150,
                    "ts": "2026-09-20T12:00:01Z",
                },
                {
                    "seq": 2,
                    "node": "Market Analyst",
                    "agent": "Market Analyst",
                    "kind": "tool",
                    "tool_name": "get_stock_data",
                    "latency_ms": 40,
                    "ts": "2026-09-20T12:00:02Z",
                },
            ],
            "started_at": "2026-09-20T12:00:00Z",
        }
        _active_runs_registry[run_aapl] = {
            "current_node": "News Analyst",
            "captured": [
                {
                    "seq": 1,
                    "node": "News Analyst",
                    "agent": "News Analyst",
                    "kind": "llm",
                    "latency_ms": 220,
                    "ts": "2026-09-20T12:00:06Z",
                },
            ],
            "started_at": "2026-09-20T12:00:05Z",
        }

    try:
        res = client.get("/api/runs/in-flight-detail")
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 2

        tickers = [d["ticker"] for d in data]
        assert "NVDA" in tickers
        assert "AAPL" in tickers

        nvda_detail = next(d for d in data if d["ticker"] == "NVDA")
        assert nvda_detail["current_node"] == "Market Analyst"
        assert nvda_detail["call_count"] == 2
        assert len(nvda_detail["latest_calls"]) == 2
        assert nvda_detail["latest_calls"][0]["ticker"] == "NVDA"

        aapl_detail = next(d for d in data if d["ticker"] == "AAPL")
        assert aapl_detail["current_node"] == "News Analyst"
        assert aapl_detail["call_count"] == 1
        assert len(aapl_detail["latest_calls"]) == 1
        assert aapl_detail["latest_calls"][0]["ticker"] == "AAPL"

    finally:
        with runner._lock:
            runner._active_runs.pop(run_nvda, None)
            runner._active_runs.pop(run_aapl, None)
        with _global_lock:
            _active_runs_registry.pop(run_nvda, None)
            _active_runs_registry.pop(run_aapl, None)


def test_in_flight_ui_contract_and_app_js():
    from pathlib import Path

    base_dir = Path(__file__).resolve().parent.parent
    index_html = (base_dir / "webapp" / "static" / "index.html").read_text()
    templates_html = (base_dir / "webapp" / "templates" / "index.html").read_text()
    app_js = (base_dir / "webapp" / "static" / "app.js").read_text()

    # 1. Elements in HTML
    for html in [index_html, templates_html]:
        assert 'id="in-flight-banner"' in html
        assert 'id="in-flight-text"' in html
        assert 'id="in-flight-badges-group"' in html
        assert 'id="in-flight-node-badge"' in html
        assert 'id="in-flight-calls-badge"' in html
        assert 'id="in-flight-links"' in html
        assert 'id="live-console-tabs"' in html
        assert 'id="live-console-stream"' in html

    # 2. app.js logic tests
    # Headline shows all in-flight tickers
    assert "Analysis in flight for ${tickerHeadline}..." in app_js
    # Badges display pills per ticker
    assert "in-flight-ticker-pill" in app_js
    assert "ticker-pill-sym" in app_js
    # Quick modal buttons clearly label ticker
    assert "Summary</button>" in app_js
    assert "Calls (" in app_js
    # Live stream console distinguishes logs/calls per ticker
    assert "live-stream-ticker-badge" in app_js
    assert "getTickerColorStyle" in app_js
    assert "setInFlightConsoleFilter" in app_js

