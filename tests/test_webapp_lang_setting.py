"""Tests for webapp frontend Language (Lang) setting UI, persistence, and propagation."""
from pathlib import Path
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient

from webapp.app import app
from webapp import db

client = TestClient(app)


def test_frontend_html_lang_setting_ui():
    """Verify Language setting dropdown with en and zh options exists in HTML templates."""
    base_dir = Path(__file__).resolve().parent.parent
    index_html = (base_dir / "webapp" / "static" / "index.html").read_text(encoding="utf-8")
    templates_html = (base_dir / "webapp" / "templates" / "index.html").read_text(encoding="utf-8")

    for path_name, html in [("static/index.html", index_html), ("templates/index.html", templates_html)]:
        assert 'id="settings-lang"' in html, f"Missing settings-lang select in {path_name}"
        assert 'value="en"' in html, f"Missing option value en in {path_name}"
        assert 'value="zh"' in html, f"Missing option value zh in {path_name}"
        assert 'id="lang-badge"' in html, f"Missing lang-badge in {path_name}"
        assert 'id="lang-badge-text"' in html, f"Missing lang-badge-text in {path_name}"


def test_frontend_app_js_lang_persistence_and_propagation():
    """Verify app.js loads and saves lang in localStorage and propagates it in API payloads."""
    base_dir = Path(__file__).resolve().parent.parent
    app_js = (base_dir / "webapp" / "static" / "app.js").read_text(encoding="utf-8")

    # LocalStorage keys & persistence functions
    assert "tradingagents_lang" in app_js
    assert "getStoredLang" in app_js
    assert "setStoredLang" in app_js
    assert "localStorage.getItem" in app_js
    assert "localStorage.setItem" in app_js

    # Settings modal bindings & rendering
    assert "settings-lang" in app_js
    assert "lang-badge-text" in app_js

    # Run parameter propagation
    assert "lang:" in app_js
    assert "triggerSingleRun" in app_js
    assert "triggerRunAllWatchlist" in app_js
    assert "api('/api/runs'" in app_js

    # Request header
    assert "X-Lang" in app_js


def test_api_settings_lang_lifecycle():
    """GET and POST /api/settings handles lang and keeps output_language in sync."""
    # Test setting to Chinese
    res_zh = client.post("/api/settings", json={"lang": "zh"})
    assert res_zh.status_code == 200
    data_zh = res_zh.json()
    assert data_zh["lang"] == "zh"
    assert data_zh["output_language"] == "Chinese"

    # Verify GET reflects Chinese
    get_zh = client.get("/api/settings")
    assert get_zh.status_code == 200
    assert get_zh.json()["lang"] == "zh"
    assert get_zh.json()["output_language"] == "Chinese"

    # Test setting to English
    res_en = client.post("/api/settings", json={"lang": "en"})
    assert res_en.status_code == 200
    data_en = res_en.json()
    assert data_en["lang"] == "en"
    assert data_en["output_language"] == "English"

    # Verify GET reflects English
    get_en = client.get("/api/settings")
    assert get_en.status_code == 200
    assert get_en.json()["lang"] == "en"
    assert get_en.json()["output_language"] == "English"


@pytest.mark.parametrize("lang, expected", [("zh", "Chinese"), ("en", "English")])
def test_runner_uses_saved_output_language(monkeypatch, lang, expected):
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph import trading_graph
    from webapp import runner as runner_module

    default_language = DEFAULT_CONFIG["output_language"]
    response = client.post("/api/settings", json={"lang": lang})
    assert response.status_code == 200
    assert db.get_all_settings()["output_language"] == expected

    graph = Mock()
    graph.return_value.propagate.return_value = ({"final_trade_decision": "HOLD"}, "HOLD")
    monkeypatch.setattr(trading_graph, "TradingAgentsGraph", graph)
    monkeypatch.setattr(runner_module, "get_alpaca_trading_client", lambda: None)
    monkeypatch.setattr(runner_module, "get_account_overview", Mock(side_effect=RuntimeError("No account")))
    monkeypatch.setattr(runner_module, "parse_trader_decision", lambda state: {"action": "HOLD"})
    monkeypatch.setattr(runner_module, "is_auto_trade_enabled", lambda: False)

    runner = runner_module.AnalysisRunner()
    # Execute submitted jobs synchronously to exercise the normal start path.
    monkeypatch.setattr(runner._executor, "submit", lambda fn, *args: fn(*args))
    try:
        run_id, success, _ = runner.start_analysis("AAPL", trade_date="2026-01-02")
    finally:
        runner._executor.shutdown(wait=True)

    assert success
    graph.assert_called_once()
    assert graph.call_args.kwargs["config"]["output_language"] == expected
    graph.return_value.propagate.assert_called_once()
    assert db.get_run(run_id)["status"] == "advisory"
    assert DEFAULT_CONFIG["output_language"] == default_language
