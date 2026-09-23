from pathlib import Path


def test_run_card_header_clean_date():
    """Verify run card header in app.js cleans up redundant Date: format and includes year."""
    app_js_path = Path("webapp/static/app.js")
    content = app_js_path.read_text(encoding="utf-8")

    # Ensure run-meta span does not contain redundant Date: ${run.trade_date}
    assert "• Date: ${run.trade_date}" not in content
    assert '<span class="run-meta">• ${formatDate(run.started_at)} • ${run.trigger}</span>' in content

    # Ensure formatDate includes year
    assert "year: 'numeric'" in content


def test_cache_busting_v10():
    """Verify ?v=20260921_v10 and ?v=20260922_v5 are used in both HTML files."""
    for path in ["webapp/templates/index.html", "webapp/static/index.html"]:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "style.css?v=20260921_v10" in content, f"Missing v10 style cache bust in {path}"
        assert "app.js?v=20260922_v5" in content, f"Missing v5 script cache bust in {path}"


def test_key_find_scrollable_css():
    """Verify .key-find-quote has scrollbar and wrapping properties."""
    css = Path("webapp/static/style.css").read_text(encoding="utf-8")
    assert ".key-find-quote" in css
    assert "max-height: 160px" in css
    assert "overflow-y: auto" in css
    assert "white-space: normal" in css
    assert "word-break: break-word" in css


def test_key_find_full_text_not_truncated():
    """Verify summary.py does not truncate long finding texts to short caps."""
    from webapp.summary import summarize_run

    long_analyst = "A" * 300
    long_debate = "B" * 500
    long_trader = "C" * 400
    long_pm = "D" * 600

    run = {
        "id": "run-test-scroll",
        "ticker": "AAPL",
        "status": "completed",
        "recommendation": {
            "action": "BUY",
            "rating": "Buy",
            "reasoning": long_trader,
        },
    }
    llm_calls = [
        {"seq": 1, "node": "Market Analyst", "kind": "llm", "ok": True, "response": long_analyst},
        {"seq": 2, "node": "Research Manager", "kind": "llm", "ok": True, "response": long_debate},
        {"seq": 3, "node": "Trader", "kind": "llm", "ok": True, "response": long_trader},
        {"seq": 4, "node": "Portfolio Manager", "kind": "llm", "ok": True, "response": f"**Executive Summary**: {long_pm}"},
    ]
    summary = summarize_run(run, llm_calls=llm_calls)
    steps = summary["steps"]

    # None of the steps should be truncated with "..."
    for s in steps:
        assert not s["key_find"].endswith("...")
    assert long_analyst in steps[0]["key_find"]
    assert long_debate in steps[1]["key_find"]
    assert long_trader in steps[2]["key_find"]
    assert long_pm in steps[3]["key_find"]
