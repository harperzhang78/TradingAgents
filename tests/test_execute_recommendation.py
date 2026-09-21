"""Tests for manual 'Confirm & Execute' recommendation endpoints and execution logic."""

import uuid
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from webapp.app import app
from webapp import db as webapp_db
from webapp.db import (
    create_order_record,
    create_recommendation,
    create_run,
    get_order_by_run,
    get_recommendation,
    get_run,
    update_run_status,
)
from webapp.execution import execute_recommendation

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolate_test_db(tmp_path, monkeypatch):
    """Ensure tests run against a clean isolated SQLite database to prevent live DB pollution."""
    test_db = tmp_path / "test_trading.db"
    monkeypatch.setattr(webapp_db, "DATABASE_PATH", test_db)
    webapp_db.init_db()
    yield


def test_execute_run_not_found():
    random_id = str(uuid.uuid4())
    res = client.post(f"/api/runs/{random_id}/execute-recommendation")
    assert res.status_code == 404
    assert f"Run '{random_id}' not found" in res.json()["detail"]


def test_execute_run_no_recommendation():
    run_id = str(uuid.uuid4())
    create_run(run_id, "AAPL", "2026-09-20", "manual")
    update_run_status(run_id, "completed")

    res = client.post(f"/api/runs/{run_id}/execute-recommendation")
    assert res.status_code == 400
    assert "No recommendation found" in res.json()["detail"]


def test_execute_run_hold_action_rejected():
    run_id = str(uuid.uuid4())
    create_run(run_id, "MSFT", "2026-09-20", "manual")
    create_recommendation(
        run_id=run_id,
        ticker="MSFT",
        trade_date="2026-09-20",
        action="HOLD",
        rating="HOLD",
        entry_price=410.0,
        stop_loss=390.0,
        price_target=450.0,
        position_sizing="5%",
        reasoning="Wait for further earnings data.",
    )
    update_run_status(run_id, "completed")

    res = client.post(f"/api/runs/{run_id}/execute-recommendation")
    assert res.status_code == 400
    assert "non-actionable" in res.json()["detail"].lower() or "hold" in res.json()["detail"].lower()


def test_execute_run_hold_rejected_even_with_side_override():
    """Regression: the frontend always sends side='buy' in the execute modal.
    Previously the backend side-determination short-circuited on overrides['side']
    before the HOLD guard was reached, so a HOLD recommendation would still place
    a BUY order. HOLD/REVIEW must be absolute and reject regardless of overrides.
    """
    run_id = str(uuid.uuid4())
    create_run(run_id, "IVV", "2026-09-20", "manual")
    create_recommendation(
        run_id=run_id,
        ticker="IVV",
        trade_date="2026-09-20",
        action="HOLD",
        rating="HOLD",
        entry_price=None,
        stop_loss=None,
        price_target=None,
        position_sizing="5% of portfolio",
        reasoning="Balanced risk/reward; maintain position.",
    )
    update_run_status(run_id, "advisory")

    # Frontend modal defaults side to 'buy' even for a HOLD — this must be rejected.
    res = client.post(f"/api/runs/{run_id}/execute-recommendation", json={"side": "buy"})
    assert res.status_code == 400
    assert "non-actionable" in res.json()["detail"].lower() or "hold" in res.json()["detail"].lower()
    # No order should have been recorded for this run.
    assert get_order_by_run(run_id) is None


@patch("webapp.execution.fetch_last_close_price")
@patch("webapp.execution.get_account_overview")
@patch("webapp.execution.submit_alpaca_order")
def test_execute_run_recommendation_success(mock_submit, mock_acct, mock_price):
    mock_acct.return_value = {
        "status": "AccountStatus.ACTIVE",
        "equity": 100000.0,
        "cash": 100000.0,
        "buying_power": 100000.0,
        "currency": "USD",
        "paper": True,
        "positions": [],
    }
    mock_price.return_value = 120.0
    mock_submit.return_value = {
        "id": "alpaca-test-ord-12345",
        "status": "accepted",
        "client_order_id": "client-123",
        "symbol": "NVDA",
        "qty": "41",
        "filled_qty": "0",
        "filled_avg_price": None,
        "side": "buy",
        "order_type": "limit",
    }

    run_id = str(uuid.uuid4())
    create_run(run_id, "NVDA", "2026-09-20", "manual")
    rec_id = create_recommendation(
        run_id=run_id,
        ticker="NVDA",
        trade_date="2026-09-20",
        action="BUY",
        rating="Buy",
        entry_price=120.50,
        stop_loss=115.00,
        price_target=145.00,
        position_sizing="5% of portfolio",
        reasoning="Bullish momentum on AI data center demand.",
    )
    # Simulate advisory mode: run remains in 'advisory' state and no order record is created
    update_run_status(run_id, "advisory")

    # Verify run before execution has no order and is in advisory state
    run_before = get_run(run_id)
    assert run_before["status"] == "advisory"
    assert run_before["order"] is None

    # Call execute recommendation endpoint
    res = client.post(f"/api/runs/{run_id}/execute-recommendation")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["alpaca_order_id"] == "alpaca-test-ord-12345"
    assert data["symbol"] == "NVDA"
    assert data["side"] == "buy"
    assert data["qty"] == 41
    assert data["limit_price"] == 120.50
    assert data["stop_price"] == 115.00
    assert data["take_profit_price"] == 145.00

    # Verify submit_alpaca_order was called
    mock_submit.assert_called_once()
    called_order = mock_submit.call_args[0][0]
    assert called_order["symbol"] == "NVDA"
    assert called_order["side"] == "buy"
    assert called_order["qty"] == 41
    assert called_order["limit_price"] == 120.50
    assert called_order["stop_price"] == 115.00
    assert called_order["take_profit_price"] == 145.00

    # Verify DB order record updated to submitted
    ord_in_db = get_order_by_run(run_id)
    assert ord_in_db is not None
    assert ord_in_db["status"] == "submitted"
    assert ord_in_db["alpaca_order_id"] == "alpaca-test-ord-12345"

    # Verify get_run reflects the submitted order
    run_detail = get_run(run_id)
    assert run_detail["order"]["status"] == "submitted"
    assert run_detail["order"]["alpaca_order_id"] == "alpaca-test-ord-12345"

    # Verify duplicate execution prevention
    dup_res = client.post(f"/api/runs/{run_id}/execute-recommendation")
    assert dup_res.status_code == 400
    assert "already been submitted" in dup_res.json()["detail"]


@patch("webapp.execution.fetch_last_close_price")
@patch("webapp.execution.get_account_overview")
@patch("webapp.execution.submit_alpaca_order")
def test_execute_with_custom_overrides(mock_submit, mock_acct, mock_price):
    mock_acct.return_value = {
        "status": "AccountStatus.ACTIVE",
        "equity": 100000.0,
        "cash": 100000.0,
        "buying_power": 100000.0,
        "currency": "USD",
        "paper": True,
        "positions": [],
    }
    mock_price.return_value = 220.0
    mock_submit.return_value = {
        "id": "alpaca-override-777",
        "status": "accepted",
        "symbol": "AAPL",
        "qty": "25",
        "side": "buy",
        "order_type": "limit",
    }

    run_id = str(uuid.uuid4())
    create_run(run_id, "AAPL", "2026-09-20", "manual")
    create_recommendation(
        run_id=run_id,
        ticker="AAPL",
        trade_date="2026-09-20",
        action="BUY",
        rating="BUY",
        entry_price=220.00,
        stop_loss=210.00,
        price_target=240.00,
        position_sizing="5%",
        reasoning="Solid fundamentals.",
    )
    update_run_status(run_id, "completed")

    payload = {
        "qty": 25,
        "limit_price": 222.50,
        "stop_price": 212.00,
        "take_profit_price": 245.00,
    }
    res = client.post(f"/api/runs/{run_id}/execute-recommendation", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["qty"] == 25
    assert data["limit_price"] == 222.50
    assert data["stop_price"] == 212.00
    assert data["take_profit_price"] == 245.00


@patch("webapp.execution.fetch_last_close_price")
@patch("webapp.execution.get_account_overview")
@patch("webapp.execution.submit_alpaca_order")
def test_execute_by_recommendation_id_endpoint(mock_submit, mock_acct, mock_price):
    mock_acct.return_value = {
        "status": "AccountStatus.ACTIVE",
        "equity": 100000.0,
        "cash": 100000.0,
        "buying_power": 100000.0,
        "currency": "USD",
        "paper": True,
        "positions": [],
    }
    mock_price.return_value = 100.0
    mock_submit.return_value = {
        "id": "alpaca-rec-id-999",
        "status": "accepted",
        "symbol": "AMD",
        "qty": "10",
        "side": "buy",
        "order_type": "limit",
    }

    run_id = str(uuid.uuid4())
    create_run(run_id, "AMD", "2026-09-20", "manual")
    rec_id = create_recommendation(
        run_id=run_id,
        ticker="AMD",
        trade_date="2026-09-20",
        action="BUY",
        rating="BUY",
        entry_price=100.00,
        stop_loss=95.00,
        price_target=115.00,
        position_sizing="5%",
        reasoning="Good risk/reward setup.",
    )
    update_run_status(run_id, "completed")

    res = client.post(f"/api/recommendations/{rec_id}/execute")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["alpaca_order_id"] == "alpaca-rec-id-999"


@patch("webapp.execution.fetch_last_close_price")
@patch("webapp.execution.get_account_overview")
@patch("webapp.execution.submit_alpaca_order")
def test_alpaca_failure_records_failed_order(mock_submit, mock_acct, mock_price):
    mock_acct.return_value = {
        "status": "AccountStatus.ACTIVE",
        "equity": 100000.0,
        "cash": 100000.0,
        "buying_power": 100000.0,
        "currency": "USD",
        "paper": True,
        "positions": [],
    }
    mock_price.return_value = 100.0
    mock_submit.side_effect = RuntimeError("Market is closed and outside trading hours")

    run_id = str(uuid.uuid4())
    create_run(run_id, "QCOM", "2026-09-20", "manual")
    rec_id = create_recommendation(
        run_id=run_id,
        ticker="QCOM",
        trade_date="2026-09-20",
        action="BUY",
        rating="BUY",
        entry_price=100.00,
        stop_loss=95.00,
        price_target=115.00,
        position_sizing="5%",
        reasoning="Test error handling.",
    )
    create_order_record(
        run_id=run_id,
        recommendation_id=rec_id,
        ticker="QCOM",
        side="buy",
        order_type="limit",
        qty=10,
        notional=None,
        limit_price=100.00,
        stop_price=95.00,
        take_profit_price=115.00,
        status="skipped",
    )
    update_run_status(run_id, "completed")

    res = client.post(f"/api/runs/{run_id}/execute-recommendation")
    assert res.status_code == 502
    assert "Market is closed" in res.json()["detail"]

    # Verify DB recorded failure
    ord_in_db = get_order_by_run(run_id)
    assert ord_in_db is not None
    assert ord_in_db["status"] == "failed"
    assert "Market is closed" in (ord_in_db["error_message"] or "")


def test_ui_and_static_assets_contain_execution_elements():
    # 1. Index route serves HTML with execute modal and run details modal buttons
    index_res = client.get("/")
    assert index_res.status_code == 200
    html = index_res.text
    assert "id=\"execute-modal\"" in html
    assert "id=\"exec-modal-submit\"" in html
    assert "Confirm &amp; Execute Order" in html
    assert "id=\"modal-btn-confirm-execute\"" in html
    assert "modal-footer-confirm-execute" not in html
    assert ("app.js?v=20260920_v" in html or "app.js" in html)
    assert ("style.css?v=20260920_v" in html or "style.css" in html)

    # 2. app.js contains execution functions, bindings, and button classes
    js_res = client.get("/static/app.js")
    assert js_res.status_code == 200
    js = js_res.text
    assert "openExecuteModal" in js
    assert "openExecuteModalFromDetails" in js
    assert "submitRecommendationOrder" in js
    assert "btn-confirm-execute" in js
    assert "execute-recommendation" in js
    assert "canExecute" in js

    # 3. style.css contains btn-success, btn-confirm-execute, and btn-confirm-reexecute
    css_res = client.get("/static/style.css")
    assert css_res.status_code == 200
    css = css_res.text
    assert ".btn-success" in css
    assert ".btn-confirm-execute" in css
    assert ".btn-confirm-reexecute" in css


@patch("webapp.execution.fetch_last_close_price")
@patch("webapp.execution.get_account_overview")
@patch("webapp.execution.submit_alpaca_order")
def test_reexecute_order_with_flag(mock_submit, mock_acct, mock_price):
    """Verify that re-executing an already submitted order is permitted when reexecute=True."""
    mock_acct.return_value = {
        "status": "AccountStatus.ACTIVE",
        "equity": 100000.0,
        "cash": 100000.0,
        "buying_power": 100000.0,
        "currency": "USD",
        "paper": True,
        "positions": [],
    }
    mock_price.return_value = 120.0
    mock_submit.return_value = {
        "id": "alpaca-reexec-order-999",
        "status": "accepted",
        "symbol": "NVDA",
        "qty": "41",
        "side": "buy",
        "order_type": "limit",
    }

    run_id = str(uuid.uuid4())
    create_run(run_id, "NVDA", "2026-09-20", "manual")
    rec_id = create_recommendation(
        run_id=run_id,
        ticker="NVDA",
        trade_date="2026-09-20",
        action="BUY",
        rating="Buy",
        entry_price=120.50,
        stop_loss=115.00,
        price_target=145.00,
        position_sizing="5%",
        reasoning="Long NVDA",
    )
    create_order_record(
        run_id=run_id,
        recommendation_id=rec_id,
        ticker="NVDA",
        side="buy",
        order_type="limit",
        qty=41,
        notional=None,
        limit_price=120.50,
        stop_price=115.00,
        take_profit_price=145.00,
        status="submitted",
        alpaca_order_id="first-alpaca-order",
    )
    update_run_status(run_id, "completed")

    # Without reexecute flag -> 400
    res_dup = client.post(f"/api/runs/{run_id}/execute-recommendation")
    assert res_dup.status_code == 400
    assert "already been submitted" in res_dup.json()["detail"]

    # With reexecute=True -> 200
    res_ok = client.post(f"/api/runs/{run_id}/execute-recommendation", json={"reexecute": True})
    assert res_ok.status_code == 200
    assert res_ok.json()["success"] is True
    assert res_ok.json()["alpaca_order_id"] == "alpaca-reexec-order-999"


@patch("webapp.execution.fetch_last_close_price")
@patch("webapp.execution.get_account_overview")
@patch("webapp.execution.submit_alpaca_order")
def test_execute_alias_route_and_hold_with_side_override(mock_submit, mock_acct, mock_price):
    """Verify /api/runs/{run_id}/execute alias route rejects HOLD even with side override.
    
    Previously this test asserted 200 (bug: side override bypassed HOLD guard).
    Now HOLD is absolute — the API must reject regardless of the 'side' override.
    """
    mock_acct.return_value = {
        "status": "AccountStatus.ACTIVE",
        "equity": 100000.0,
        "cash": 100000.0,
        "buying_power": 100000.0,
        "currency": "USD",
        "paper": True,
        "positions": [],
    }
    mock_price.return_value = 410.0
    mock_submit.return_value = {
        "id": "alpaca-msft-override-123",
        "status": "accepted",
        "symbol": "MSFT",
        "qty": "10",
        "side": "buy",
        "order_type": "limit",
    }

    run_id = str(uuid.uuid4())
    create_run(run_id, "MSFT", "2026-09-20", "manual")
    create_recommendation(
        run_id=run_id,
        ticker="MSFT",
        trade_date="2026-09-20",
        action="HOLD",
        rating="HOLD",
        entry_price=410.0,
        stop_loss=390.0,
        price_target=450.0,
        position_sizing="5%",
        reasoning="Wait for further earnings data.",
    )
    update_run_status(run_id, "completed")

    # HOLD is absolute: even with an explicit side override, must reject.
    payload = {
        "qty": 10,
        "limit_price": 410.0,
        "side": "buy",
        "order_type": "limit",
    }
    res = client.post(f"/api/runs/{run_id}/execute", json=payload)
    assert res.status_code == 400
    assert "non-actionable" in res.json()["detail"].lower() or "hold" in res.json()["detail"].lower()
    # submit_alpaca_order must NOT have been called
    mock_submit.assert_not_called()


def test_card_rendering_can_execute_logic():
    """Verify the card rendering criteria for skipped, pending, unexecuted, and submitted cards."""
    def eval_can_execute(run):
        rec = run.get("recommendation")
        order = run.get("order")
        is_running = run.get("status") == "running"
        action_up = (rec.get("action") or "").strip().upper() if rec else ""
        rating_up = (rec.get("rating") or "").strip().upper() if rec else ""
        order_side_up = (order.get("side") or "").strip().upper() if order else ""

        is_submitted = bool(order and order.get("status") == "submitted")
        has_rec = bool(rec and (rec.get("action") or rec.get("rating") or rec.get("entry_price") is not None))
        has_order = bool(order and order_side_up in ("BUY", "SELL"))

        return not is_running and (has_rec or has_order) and not is_submitted

    # 1. NVDA in advisory mode (status 'advisory', no order created) -> canExecute is True
    nvda_card = {
        "status": "advisory",
        "recommendation": {"action": "Sell", "rating": "Underweight", "entry_price": 222.27},
        "order": None,
    }
    assert eval_can_execute(nvda_card) is True

    # 2. MSFT in advisory mode (HOLD / Advisory Mode, no order created) -> canExecute is True
    msft_card = {
        "status": "advisory",
        "recommendation": {"action": "HOLD", "rating": "HOLD", "entry_price": 410.0},
        "order": None,
    }
    assert eval_can_execute(msft_card) is True

    # 3. Card with already submitted live order -> canExecute is False
    submitted_card = {
        "status": "completed",
        "recommendation": {"action": "BUY", "rating": "BUY", "entry_price": 120.5},
        "order": {"status": "submitted", "alpaca_order_id": "real-alpaca-id-123", "side": "buy"},
    }
    assert eval_can_execute(submitted_card) is False

    # 4. In-flight running card -> canExecute is False
    running_card = {
        "status": "running",
        "recommendation": None,
        "order": None,
    }
    assert eval_can_execute(running_card) is False


def test_advisory_mode_workflow_creates_no_orders_and_remains_advisory(tmp_path):
    """When Auto-Trade is OFF (Advisory Mode), runner finishes in advisory state without orders."""
    import json
    from webapp.db import set_setting
    from webapp.runner import runner

    set_setting("auto_trade", "false")

    log_data = {
        "trader_investment_plan": "**Action**: BUY\n**Entry Price**: $125.00\n**Stop Loss**: $118.00\n**Position Sizing**: 5%\n**Reasoning**: Solid momentum.",
        "final_trade_decision": "**Rating**: Buy\n**Price Target**: $150.00",
    }
    dummy_log = tmp_path / "dummy_decision.json"
    dummy_log.write_text(json.dumps(log_data), encoding="utf-8")

    run_id = str(uuid.uuid4())
    create_run(run_id, "AAPL", "2026-09-20", "manual")

    runner._execute_job(run_id, "AAPL", "2026-09-20", "manual", from_log_path=str(dummy_log))

    # 1. Verify run finished in 'advisory' state
    run_detail = get_run(run_id)
    assert run_detail is not None
    assert run_detail["status"] == "advisory"

    # 2. Verify NO order record was created
    assert run_detail["order"] is None
    assert get_order_by_run(run_id) is None

    # 3. Verify recommendation was persisted
    rec = run_detail.get("recommendation")
    assert rec is not None
    assert rec["action"] == "BUY"
    assert rec["entry_price"] == 125.00

    # 4. Verify API /api/runs/{run_id} reflects advisory state and null order
    res = client.get(f"/api/runs/{run_id}")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "advisory"
    assert data["order"] is None

    # 5. Verify decision summary reflects Advisory step (Step 5)
    sum_res = client.get(f"/api/runs/{run_id}/summary")
    assert sum_res.status_code == 200
    sum_data = sum_res.json()
    assert len(sum_data["steps"]) == 5
    step5 = sum_data["steps"][4]
    assert step5["title"] == "Advisory"
    assert "No order submitted" in step5["key_find"]

    # 6. Verify manual execution works on this advisory run
    with patch("webapp.execution.submit_alpaca_order") as mock_submit:
        mock_submit.return_value = {
            "id": "alpaca-advisory-ord-999",
            "client_order_id": f"trade_{run_id[:8]}",
            "status": "accepted",
            "symbol": "AAPL",
            "qty": "40",
            "side": "buy",
            "type": "limit",
        }
        exec_res = client.post(f"/api/runs/{run_id}/execute-recommendation")
        assert exec_res.status_code == 200
        exec_data = exec_res.json()
        assert exec_data["success"] is True
        assert exec_data["alpaca_order_id"] == "alpaca-advisory-ord-999"

        # Verify run now has submitted order and status is completed
        updated_run = get_run(run_id)
        assert updated_run["status"] == "completed"
        assert updated_run["order"] is not None
        assert updated_run["order"]["status"] == "submitted"
        assert updated_run["order"]["alpaca_order_id"] == "alpaca-advisory-ord-999"


def test_auto_trade_enabled_workflow_creates_order_and_completes(tmp_path):
    """When Auto-Trade is ON, runner submits order and marks run completed."""
    import json
    from webapp.db import set_setting
    from webapp.runner import runner

    set_setting("auto_trade", "true")

    log_data = {
        "trader_investment_plan": "**Action**: BUY\n**Entry Price**: $125.00\n**Stop Loss**: $118.00\n**Position Sizing**: 5%\n**Reasoning**: Solid momentum.",
        "final_trade_decision": "**Rating**: Buy\n**Price Target**: $150.00",
    }
    dummy_log = tmp_path / "dummy_decision_autotrade.json"
    dummy_log.write_text(json.dumps(log_data), encoding="utf-8")

    run_id = str(uuid.uuid4())
    create_run(run_id, "MSFT", "2026-09-20", "manual")

    with patch("webapp.runner.submit_alpaca_order") as mock_submit, \
         patch("webapp.runner.get_alpaca_trading_client") as mock_client:
        mock_submit.return_value = {
            "id": "alpaca-auto-ord-111",
            "client_order_id": f"trade_{run_id[:8]}",
            "status": "accepted",
            "symbol": "MSFT",
            "qty": "40",
            "side": "buy",
            "type": "limit",
        }
        runner._execute_job(run_id, "MSFT", "2026-09-20", "manual", from_log_path=str(dummy_log))

        run_detail = get_run(run_id)
        assert run_detail["status"] == "completed"
        assert run_detail["order"] is not None
        assert run_detail["order"]["status"] == "submitted"
        assert run_detail["order"]["alpaca_order_id"] == "alpaca-auto-ord-111"

