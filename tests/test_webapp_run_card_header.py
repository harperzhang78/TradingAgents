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


def test_cache_busting_v9():
    """Verify ?v=20260921_v9 is used in both HTML files."""
    for path in ["webapp/templates/index.html", "webapp/static/index.html"]:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "style.css?v=20260921_v9" in content, f"Missing v9 style cache bust in {path}"
        assert "app.js?v=20260921_v9" in content, f"Missing v9 script cache bust in {path}"
