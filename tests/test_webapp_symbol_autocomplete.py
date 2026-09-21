"""Tests for symbol autocomplete and search functionality."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from webapp.app import app
from webapp.db import add_watchlist_item, remove_watchlist_item
from webapp.symbols import (
    ALIASES,
    FALLBACK_SYMBOLS,
    POPULAR_TICKERS,
    clear_symbol_cache,
    get_symbol_info,
    load_symbols,
    search_symbols,
)

client = TestClient(app)


class TestSymbolSearchUnit:
    """Unit tests for search_symbols and get_symbol_info."""

    def test_empty_query_returns_empty_list(self):
        assert search_symbols("") == []
        assert search_symbols("   ") == []

    def test_exact_ticker_match(self):
        results = search_symbols("AAPL")
        assert len(results) > 0
        assert results[0]["symbol"] == "AAPL"
        assert "Apple" in results[0]["name"]

    def test_ticker_prefix_match(self):
        results = search_symbols("NV")
        symbols = [r["symbol"] for r in results]
        assert "NVDA" in symbols
        # NVDA is a popular ticker, should be prioritized near top
        assert symbols.index("NVDA") <= 2

    def test_company_name_match(self):
        results = search_symbols("Apple")
        assert len(results) > 0
        symbols = [r["symbol"] for r in results]
        assert "AAPL" in symbols

        res_msft = search_symbols("Microsoft")
        assert len(res_msft) > 0
        assert res_msft[0]["symbol"] == "MSFT"

        res_tsla = search_symbols("Tesla")
        assert len(res_tsla) > 0
        assert res_tsla[0]["symbol"] == "TSLA"

    def test_company_name_partial_word(self):
        results = search_symbols("amaz")
        symbols = [r["symbol"] for r in results]
        assert "AMZN" in symbols

    def test_aliases_matching(self):
        # 'google' alias should match GOOGL / GOOG
        res_goog = search_symbols("google")
        goog_syms = [r["symbol"] for r in res_goog]
        assert "GOOGL" in goog_syms or "GOOG" in goog_syms

        # 'facebook' alias should match META
        res_fb = search_symbols("facebook")
        fb_syms = [r["symbol"] for r in res_fb]
        assert "META" in fb_syms

        # 's&p' alias should match SPY, VOO, and IVV
        res_sp = search_symbols("s&p")
        sp_syms = [r["symbol"] for r in res_sp]
        assert "SPY" in sp_syms
        assert "VOO" in sp_syms
        assert "IVV" in sp_syms

        # 's&p 500' and 'sp500' aliases should also include IVV
        res_sp500 = search_symbols("s&p 500")
        assert "IVV" in [r["symbol"] for r in res_sp500]

        res_sp500_nospace = search_symbols("sp500")
        assert "IVV" in [r["symbol"] for r in res_sp500_nospace]

        # 'nasdaq' alias should match QQQ
        res_nasdaq = search_symbols("nasdaq")
        nasdaq_syms = [r["symbol"] for r in res_nasdaq]
        assert "QQQ" in nasdaq_syms

    def test_ivv_etf_support(self):
        """Verify IVV is included in POPULAR_TICKERS, FALLBACK_SYMBOLS, ALIASES, and search."""
        assert "IVV" in POPULAR_TICKERS
        assert "IVV" in ALIASES["s&p"]
        assert "IVV" in ALIASES["s&p 500"]
        assert "IVV" in ALIASES["sp500"]
        assert any(
            item["symbol"] == "IVV" and item["name"] == "iShares Core S&P 500 ETF"
            for item in FALLBACK_SYMBOLS
        )

        # Exact match
        results = search_symbols("IVV")
        assert len(results) > 0
        assert results[0]["symbol"] == "IVV"
        assert results[0]["name"] == "iShares Core S&P 500 ETF"

        # Lookup by ticker info
        info = get_symbol_info("IVV")
        assert info is not None
        assert info["symbol"] == "IVV"
        assert info["name"] == "iShares Core S&P 500 ETF"

        # Search by company/fund name prefix
        name_results = search_symbols("iShares Core")
        assert any(r["symbol"] == "IVV" for r in name_results)

    def test_popular_major_etfs_coverage(self):
        """Verify key popular ETFs across all major categories are thoroughly covered."""
        major_etfs = [
            # S&P 500 & Broad Market
            "IVV", "VOO", "SPY", "SPLG", "RSP", "VTI", "ITOT", "SCHB",
            # Nasdaq & Dow
            "QQQ", "QQQM", "DIA",
            # Growth & Value
            "VUG", "VTV", "IWF", "IWD", "SCHG",
            # Mid & Small Cap
            "IWM", "IJH", "IJR", "VB", "VO",
            # International & Emerging Markets
            "VXUS", "VEA", "IEFA", "VWO", "IEMG", "VT",
            # Fixed Income & Bonds
            "BND", "AGG", "TLT", "IEF", "SHY", "BIL", "SGOV", "HYG", "LQD",
            # Dividend
            "SCHD", "VYM", "VIG", "JEPI", "JEPQ",
            # Sector & Real Estate
            "XLK", "XLF", "XLV", "XLE", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC", "VNQ",
            # Thematic & Commodities
            "SMH", "SOXX", "XBI", "IBB", "KRE", "ARKK", "GLD", "SLV", "IAU", "USO",
        ]
        for ticker in major_etfs:
            assert ticker in POPULAR_TICKERS, f"{ticker} should be in POPULAR_TICKERS"
            info = get_symbol_info(ticker)
            assert info is not None, f"get_symbol_info should resolve {ticker}"
            assert info["symbol"] == ticker
            assert len(info["name"]) > 0

            # Exact search should return the ETF as top result
            results = search_symbols(ticker)
            assert len(results) > 0, f"search_symbols({ticker}) should return results"
            assert results[0]["symbol"] == ticker

    def test_etf_family_and_category_aliases(self):
        """Verify ETF provider brand names and category queries resolve expected ETFs."""
        test_cases = {
            "ishares": ["IVV", "IEFA", "IEMG", "IWM", "AGG", "TLT"],
            "vanguard": ["VOO", "VTI", "VXUS", "BND", "VUG"],
            "invesco": ["QQQ", "QQQM"],
            "schwab": ["SCHD"],
            "spdr": ["SPY", "DIA", "XLK", "XLC"],
            "dividend": ["SCHD", "VYM", "JEPI"],
            "bonds": ["BND", "AGG", "TLT"],
            "biotech": ["XBI", "IBB"],
            "semiconductor": ["SMH", "SOXX"],
            "real estate": ["VNQ", "XLRE"],
        }
        for query, expected_symbols in test_cases.items():
            results = search_symbols(query, limit=15)
            symbols = [r["symbol"] for r in results]
            for exp in expected_symbols:
                assert exp in symbols, f"Query '{query}' should return {exp}, got: {symbols}"

    def test_ticker_prefix_prioritization_for_etfs(self):
        """Verify popular ETFs are prioritized when searching partial ticker prefix."""
        # 'IV' should prioritize 'IVV' over obscure penny stocks
        res_iv = search_symbols("IV", limit=5)
        symbols_iv = [r["symbol"] for r in res_iv]
        assert "IVV" in symbols_iv
        assert symbols_iv[0] == "IVV"

        # 'QQ' should prioritize 'QQQ'
        res_qq = search_symbols("QQ", limit=5)
        symbols_qq = [r["symbol"] for r in res_qq]
        assert "QQQ" in symbols_qq
        assert symbols_qq[0] == "QQQ"

        # 'SC' should include 'SCHD' near top
        res_sc = search_symbols("SC", limit=10)
        symbols_sc = [r["symbol"] for r in res_sc]
        assert "SCHD" in symbols_sc

    def test_fallback_symbols_etf_coverage(self):
        """Verify FALLBACK_SYMBOLS covers all core ETFs and fallback loading works."""
        fallback_tickers = {item["symbol"] for item in FALLBACK_SYMBOLS}
        core_etfs = [
            "IVV", "SPY", "VOO", "QQQ", "QQQM", "DIA", "VTI", "VXUS",
            "BND", "AGG", "TLT", "SCHD", "XLC", "VNQ"
        ]
        for etf in core_etfs:
            assert etf in fallback_tickers, f"FALLBACK_SYMBOLS must contain {etf}"

    def test_fallback_symbols_loading(self, monkeypatch):
        """Verify FALLBACK_SYMBOLS contains IVV when symbols.json is absent."""
        from pathlib import Path
        import webapp.symbols as sym_module

        saved_cache = sym_module._SYMBOLS_CACHE
        saved_map = sym_module._SYMBOLS_MAP
        try:
            sym_module._SYMBOLS_CACHE = None
            sym_module._SYMBOLS_MAP = {}
            monkeypatch.setattr(sym_module, "SYMBOLS_FILE", Path("/non/existent/file.json"))
            loaded = sym_module.load_symbols()
            ivv_items = [s for s in loaded if s["symbol"] == "IVV"]
            assert len(ivv_items) == 1
            assert ivv_items[0]["name"] == "iShares Core S&P 500 ETF"
            assert sym_module.get_symbol_map().get("IVV") == "iShares Core S&P 500 ETF"
        finally:
            sym_module._SYMBOLS_CACHE = saved_cache
            sym_module._SYMBOLS_MAP = saved_map

    def test_case_insensitivity(self):
        res_lower = search_symbols("aapl")
        res_upper = search_symbols("AAPL")
        res_mixed = search_symbols("AaPl")
        assert [r["symbol"] for r in res_lower] == [r["symbol"] for r in res_upper]
        assert [r["symbol"] for r in res_lower] == [r["symbol"] for r in res_mixed]

    def test_limit_enforcement(self):
        results = search_symbols("A", limit=3)
        assert len(results) <= 3
        results_1 = search_symbols("A", limit=1)
        assert len(results_1) == 1

    def test_get_symbol_info(self):
        info = get_symbol_info("AAPL")
        assert info is not None
        assert info["symbol"] == "AAPL"
        assert "Apple" in info["name"]

        unknown = get_symbol_info("NONEXISTENT_99999")
        assert unknown is None

    def test_dynamic_watchlist_symbol_inclusion(self):
        custom_ticker = "MYCUSTOMTESTXYZ"
        custom_notes = "My Custom Unicorn Startup"
        try:
            add_watchlist_item(custom_ticker, custom_notes)
            results = search_symbols("MYCUSTOMTEST")
            syms = [r["symbol"] for r in results]
            assert custom_ticker in syms
            matched = next(r for r in results if r["symbol"] == custom_ticker)
            assert custom_notes in matched["name"]
        finally:
            remove_watchlist_item(custom_ticker)


class TestSymbolAPIEndpoints:
    """API integration tests for /api/symbols routes."""

    def test_api_symbols_search(self):
        resp = client.get("/api/symbols/search?q=apple")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        symbols = [item["symbol"] for item in data]
        assert "AAPL" in symbols

    def test_api_symbols_search_alias_route(self):
        resp = client.get("/api/symbols?q=nvda")
        assert resp.status_code == 200
        data = resp.json()
        symbols = [item["symbol"] for item in data]
        assert "NVDA" in symbols

    def test_api_symbols_search_empty_query(self):
        resp = client.get("/api/symbols/search?q=")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_api_symbols_search_limit(self):
        resp = client.get("/api/symbols/search?q=a&limit=4")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) <= 4

    def test_api_symbol_detail_success(self):
        resp = client.get("/api/symbols/AAPL")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "AAPL"
        assert "Apple" in data["name"]

    def test_api_symbol_detail_not_found(self):
        resp = client.get("/api/symbols/NOTAREALTICKER12345")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_api_symbol_detail_ivv(self):
        resp = client.get("/api/symbols/IVV")
        assert resp.status_code == 200
        data = resp.json()
        assert data["symbol"] == "IVV"
        assert data["name"] == "iShares Core S&P 500 ETF"

    def test_api_symbols_search_sp500_includes_ivv(self):
        resp = client.get("/api/symbols/search?q=s%26p")
        assert resp.status_code == 200
        data = resp.json()
        symbols = [item["symbol"] for item in data]
        assert "IVV" in symbols

    def test_api_symbols_search_etf_queries(self):
        resp_ishares = client.get("/api/symbols/search?q=ishares")
        assert resp_ishares.status_code == 200
        syms_ishares = [item["symbol"] for item in resp_ishares.json()]
        assert "IVV" in syms_ishares

        resp_schwab = client.get("/api/symbols/search?q=schd")
        assert resp_schwab.status_code == 200
        syms_schwab = [item["symbol"] for item in resp_schwab.json()]
        assert "SCHD" in syms_schwab

        resp_vanguard = client.get("/api/symbols/search?q=vanguard")
        assert resp_vanguard.status_code == 200
        syms_vanguard = [item["symbol"] for item in resp_vanguard.json()]
        assert "VOO" in syms_vanguard

    def test_api_symbol_detail_major_etfs(self):
        for ticker in ["IVV", "SCHD", "VXUS", "XLC", "QQQM"]:
            resp = client.get(f"/api/symbols/{ticker}")
            assert resp.status_code == 200
            data = resp.json()
            assert data["symbol"] == ticker
            assert len(data["name"]) > 0


class TestSymbolAutocompleteUI:
    """Frontend asset and contract tests for symbol autocomplete."""

    def test_html_contains_autocomplete_elements_and_aria_roles(self):
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.text

        assert 'id="symbol-autocomplete-wrap"' in html
        assert 'id="input-symbol"' in html
        assert 'id="symbol-autocomplete-list"' in html
        assert 'role="combobox"' in html
        assert 'role="listbox"' in html
        assert 'aria-autocomplete="list"' in html
        assert 'aria-controls="symbol-autocomplete-list"' in html
        assert 'placeholder="Symbol or company' in html

    def test_app_js_contains_autocomplete_controller_and_handlers(self):
        resp = client.get("/static/app.js")
        assert resp.status_code == 200
        js = resp.text

        assert "initSymbolAutocomplete" in js
        assert "fetchAutocompleteSuggestions" in js
        assert "selectAutocompleteItem" in js
        assert "closeAutocompleteDropdown" in js
        assert "renderAutocompleteSuggestions" in js
        assert "autocompleteState" in js
        assert "highlightMatch" in js
        assert "/api/symbols/search" in js

    def test_style_css_contains_autocomplete_and_responsive_rules(self):
        resp = client.get("/static/style.css")
        assert resp.status_code == 200
        css = resp.text

        assert ".autocomplete-wrap" in css
        assert ".autocomplete-dropdown" in css
        assert ".autocomplete-item" in css
        assert ".autocomplete-symbol" in css
        assert ".autocomplete-name" in css
        assert ".autocomplete-highlight" in css
        # Check media queries define responsive adjustments
        assert "@media (max-width: 768px)" in css
        assert "@media (max-width: 480px)" in css

