"""Tests for Decision Rationale Dialog and Trader Action authoritative resolution.

Verifies:
1. build_order: explicit Action=HOLD or REVIEW never generates an order regardless of PM rating.
   Empty/missing action falls back to PM rating. Conflict message is logged.
2. execute_recommendation: rejects Action=HOLD / REVIEW even with overrides or bullish rating.
3. POST /api/runs/{run_id}/ask endpoint: returns LLM answer, handles 404, 400, 503 unconfigured.
4. build_run_rationale_context: correctly formats run metadata, conflict note, timeline, agent calls.
"""
from __future__ import annotations

import logging
import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from webapp import db as webapp_db
from webapp import llm_settings
from webapp.app import app
from webapp.db import (
    create_order_record,
    create_recommendation,
    create_run,
    get_run,
    insert_llm_calls,
    set_setting,
    update_run_status,
)
from webapp.execution import build_order, execute_recommendation
from webapp.summary import build_run_rationale_context, summarize_run

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_test_db(tmp_path, monkeypatch):
    """Ensure tests run against a clean isolated SQLite database."""
    test_db = tmp_path / "test_trading.db"
    monkeypatch.setattr(webapp_db, "DATABASE_PATH", test_db)
    webapp_db.init_db()
    yield


# ---------------------------------------------------------------------------
# Part 1: build_order & execute_recommendation Trader Action Authority Tests
# ---------------------------------------------------------------------------

def test_build_order_explicit_hold_with_overweight_rating_returns_none(caplog):
    """When Trader explicitly says Action=HOLD and PM Rating=Overweight, build_order MUST return None."""
    decision = {
        "action": "HOLD",
        "rating": "Overweight",
        "entry_price": 120.0,
        "stop_loss": 110.0,
        "price_target": 140.0,
        "position_sizing": "5% of portfolio",
        "reasoning": "No current position, avoid entry due to high valuation.",
    }
    with caplog.at_level(logging.WARNING):
        order = build_order("NVDA", decision, account_equity=100000.0, current_price=120.0, held_qty=0)

    assert order is None
    # Verify conflict message was logged
    assert any("冲突" in record.message and "Action=HOLD" in record.message for record in caplog.records)


def test_build_order_explicit_review_with_buy_rating_returns_none(caplog):
    """When Trader explicitly says Action=REVIEW and PM Rating=BUY, build_order MUST return None."""
    decision = {
        "action": "REVIEW",
        "rating": "BUY",
        "entry_price": 100.0,
    }
    with caplog.at_level(logging.WARNING):
        order = build_order("AAPL", decision, account_equity=100000.0, current_price=100.0)

    assert order is None
    assert any("冲突" in record.message and "Action=REVIEW" in record.message for record in caplog.records)


def test_build_order_explicit_hold_with_underweight_rating_returns_none():
    """When Trader explicitly says Action=HOLD and PM Rating=Underweight, build_order returns None."""
    decision = {
        "action": "HOLD",
        "rating": "Underweight",
        "entry_price": 200.0,
    }
    order = build_order("TSLA", decision, account_equity=100000.0, current_price=200.0, held_qty=50)
    assert order is None


def test_build_order_empty_action_falls_back_to_pm_rating():
    """When Trader gives no action (empty/missing), fall back to PM rating."""
    decision_buy = {
        "action": "",
        "rating": "Overweight",
        "entry_price": 150.0,
        "stop_loss": 140.0,
        "price_target": 180.0,
    }
    order = build_order("AAPL", decision_buy, account_equity=100000.0, current_price=150.0)
    assert order is not None
    assert order["side"] == "buy"
    assert order["limit_price"] == 150.0

    decision_none = {
        "action": None,
        "rating": "BUY",
        "entry_price": 150.0,
    }
    order2 = build_order("AAPL", decision_none, account_equity=100000.0, current_price=150.0)
    assert order2 is not None
    assert order2["side"] == "buy"


def test_build_order_empty_action_with_sell_rating_checks_position():
    """When Trader gives no action and PM says Underweight, flat positions must NOT place sell orders."""
    decision_sell = {
        "action": "",
        "rating": "Underweight",
        "entry_price": 100.0,
    }
    # Flat position (held_qty=0) -> returns None (Avoid Entry)
    assert build_order("MSFT", decision_sell, 100000.0, current_price=100.0, held_qty=0) is None

    # Long position (held_qty=10) -> generates sell order
    order = build_order("MSFT", decision_sell, 100000.0, current_price=100.0, held_qty=10)
    assert order is not None
    assert order["side"] == "sell"
    assert order["qty"] <= 10


def test_execute_recommendation_hold_with_overweight_rating_rejected():
    """execute_recommendation rejects explicit Action=HOLD even if PM Rating=Overweight and side='buy'."""
    rec = {
        "ticker": "NVDA",
        "action": "HOLD",
        "rating": "Overweight",
        "entry_price": 120.0,
        "current_position_qty": 0,
    }
    with pytest.raises(ValueError, match=r"Trader Action is 'HOLD'"):
        execute_recommendation(rec, run_id="dummy-run", overrides={"side": "buy"})


# ---------------------------------------------------------------------------
# Part 2: Context Builder & Ask Endpoint Tests
# ---------------------------------------------------------------------------

def test_build_run_rationale_context_includes_conflict_and_steps():
    """build_run_rationale_context produces structured context including conflict warning and steps."""
    run = {
        "id": "run-test-1",
        "ticker": "NVDA",
        "trade_date": "2026-09-20",
        "status": "completed",
    }
    rec = {
        "action": "HOLD",
        "rating": "Overweight",
        "entry_price": 120.0,
        "stop_loss": 110.0,
        "price_target": 140.0,
        "position_sizing": "5%",
        "current_position_qty": 0,
        "reasoning": "Trader chose to avoid entry given neutral technical setup.",
        "trader_investment_plan": "Action: HOLD. No existing position. Avoid entering at current highs.",
        "final_trade_decision": "Rating: Overweight. Long term AI momentum is intact.",
    }
    summary = {
        "overall": "HOLD NVDA — Risk/reward balanced",
        "confidence": "Overweight",
        "steps": [
            {"n": 1, "name": "Market & Fundamentals", "who": "Market Analyst", "verdict": "Bullish", "key_find": "Strong revenue growth."},
            {"n": 2, "name": "Consensus Debate", "who": "Research Manager", "verdict": "Hold", "key_find": "Bulls and Bears clashed over valuation."},
        ],
    }
    calls = [
        {"seq": 1, "node": "market_analyst", "response": "Revenue up 40% year on year.", "ok": True},
        {"seq": 2, "node": "trader", "response": "Action: HOLD. We have no position and entry risk is elevated.", "ok": True},
    ]
    order = {"status": "skipped", "skip_reason": "⚠️ 冲突：Trader Action=HOLD 但 PM Rating=Overweight — 以 Trader 的 HOLD 为准，未下单"}

    context = build_run_rationale_context(run, summary, rec, order, calls)

    assert "Ticker: NVDA" in context
    assert "Trader Action: HOLD" in context
    assert "Portfolio Manager Rating: Overweight" in context
    assert "NOTABLE CONFLICT" in context
    assert "Trader Action is 'HOLD' while Portfolio Manager Rating is 'Overweight'" in context
    assert "MARKET_ANALYST" in context
    assert "Revenue up 40%" in context
    assert "Consensus Debate" in context


def test_ask_endpoint_run_not_found():
    """POST /api/runs/{run_id}/ask returns 404 for unknown run."""
    res = client.post(f"/api/runs/{uuid.uuid4()}/ask", json={"question": "Why?"})
    assert res.status_code == 404
    assert "not found" in res.json()["detail"].lower()


def test_ask_endpoint_empty_question():
    """POST /api/runs/{run_id}/ask returns 400 for empty question."""
    run_id = str(uuid.uuid4())
    create_run(run_id, "AAPL", "2026-09-20", "manual")
    res = client.post(f"/api/runs/{run_id}/ask", json={"question": "   "})
    assert res.status_code == 400
    assert "empty" in res.json()["detail"].lower()


def test_ask_endpoint_unconfigured_llm(monkeypatch):
    """POST /api/runs/{run_id}/ask returns 503 if LLM is not configured."""
    run_id = str(uuid.uuid4())
    create_run(run_id, "AAPL", "2026-09-20", "manual")
    set_setting(llm_settings.SETTING_PROVIDER, "openai")
    set_setting(llm_settings.SETTING_API_KEY, "")
    for env_var in llm_settings.PROVIDER_KEY_ENV.values():
        if env_var:
            monkeypatch.delenv(env_var, raising=False)

    res = client.post(f"/api/runs/{run_id}/ask", json={"question": "Why did we buy AAPL?"})
    assert res.status_code == 503
    assert "not configured" in res.json()["detail"].lower()


@patch("tradingagents.llm_clients.create_llm_client")
def test_ask_endpoint_success(mock_create_client):
    """POST /api/runs/{run_id}/ask successfully invokes LLM and returns answer."""
    mock_llm = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "Trader chose HOLD because you do not hold a long position in NVDA, and entering at current peak multiples presents an unfavorable risk-reward ratio."
    mock_llm.invoke.return_value = mock_response

    mock_client_instance = MagicMock()
    mock_client_instance.get_llm.return_value = mock_llm
    mock_create_client.return_value = mock_client_instance

    run_id = str(uuid.uuid4())
    create_run(run_id, "NVDA", "2026-09-20", "manual")
    create_recommendation(
        run_id=run_id,
        ticker="NVDA",
        trade_date="2026-09-20",
        action="HOLD",
        rating="Overweight",
        entry_price=120.0,
        stop_loss=110.0,
        price_target=140.0,
        position_sizing="5%",
        reasoning="Avoid entry: no position held and technical momentum stalled.",
    )
    update_run_status(run_id, "completed")
    insert_llm_calls(
        run_id=run_id,
        calls=[{
            "seq": 1,
            "node": "trader",
            "agent": "Trader",
            "kind": "llm",
            "model": "gpt-5.6-luna",
            "request": {"prompt": "Decide"},
            "response": "Action: HOLD. No position, avoid entering at local tops.",
            "ok": True,
        }],
    )

    # Configure mock provider and key in settings
    set_setting(llm_settings.SETTING_PROVIDER, "openai")
    set_setting(llm_settings.SETTING_API_KEY, "sk-test-key-12345")
    set_setting(llm_settings.SETTING_QUICK_MODEL, "gpt-5.6-luna")

    res = client.post(
        f"/api/runs/{run_id}/ask",
        json={"question": "为什么 Trader 选择了 HOLD 而不是买入？"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "answer" in data
    assert "Trader chose HOLD" in data["answer"]
    assert data["run_id"] == run_id
    assert data["ticker"] == "NVDA"

    # Verify mock_llm.invoke was called with the context and question
    assert mock_llm.invoke.called
    invoke_args = mock_llm.invoke.call_args[0][0]
    # Check messages structure: list of ("system", prompt), ("human", question)
    assert invoke_args[0][0] == "system"
    assert "NVDA" in invoke_args[0][1]
    assert "HOLD" in invoke_args[0][1]
    assert invoke_args[1] == ("human", "为什么 Trader 选择了 HOLD 而不是买入？")


@patch("tradingagents.llm_clients.create_llm_client")
def test_ask_endpoint_llm_failure_returns_500(mock_create_client):
    """POST /api/runs/{run_id}/ask returns 500 when LLM call throws an exception."""
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = RuntimeError("API connection timeout to provider")
    mock_client_instance = MagicMock()
    mock_client_instance.get_llm.return_value = mock_llm
    mock_create_client.return_value = mock_client_instance

    run_id = str(uuid.uuid4())
    create_run(run_id, "AAPL", "2026-09-20", "manual")
    set_setting(llm_settings.SETTING_PROVIDER, "openai")
    set_setting(llm_settings.SETTING_API_KEY, "sk-test-key")

    res = client.post(f"/api/runs/{run_id}/ask", json={"question": "Why did we buy?"})
    assert res.status_code == 500
    assert "failed to query llm" in res.json()["detail"].lower()
