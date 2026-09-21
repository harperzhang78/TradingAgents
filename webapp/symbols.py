"""Stock symbol search and autocomplete service for TradingAgents."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
SYMBOLS_FILE = DATA_DIR / "symbols.json"

# Top well-known tickers to prioritize in search rankings
POPULAR_TICKERS: set[str] = {
    # Mega-cap and widely held equities
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA",
    "BRK.B", "BRK.A", "LLY", "AVGO", "JPM", "V", "UNH", "XOM", "MA",
    "JNJ", "PG", "HD", "COST", "ABBV", "MRK", "CRM", "BAC", "CVX",
    "NFLX", "AMD", "WMT", "PEP", "KO", "LIN", "TMO", "ADBE", "CSCO",
    "ACN", "ORCL", "MCD", "DIS", "ABT", "GE", "QCOM", "INTC", "TXN",
    "VZ", "CAT", "IBM", "NKE", "AMAT", "COP", "NOW", "PM", "GS", "MS",
    "PLTR", "UBER", "COIN", "SNOW", "MSTR", "ARM", "CRWD", "PANW",
    "TSM", "ASML", "BABA", "NVO", "BA", "LMT", "NEE", "MU", "SPOT",
    # Core & Broad Market ETFs
    "SPY", "IVV", "VOO", "SPLG", "RSP", "QQQ", "QQQM", "DIA", "VTI", "ITOT", "SCHB",
    # Growth, Value & Size ETFs
    "VUG", "VTV", "IWF", "IWD", "IWB", "SCHG", "SCHV",
    "IWM", "IJH", "IJR", "VB", "VO", "VXF", "SCHA",
    # International & Emerging Markets ETFs
    "VXUS", "VEA", "IEFA", "VWO", "IEMG", "VT", "EEM", "EFA", "IXUS", "ACWI",
    # Fixed Income, Treasuries & Bond ETFs
    "BND", "AGG", "BNDX", "TLT", "IEF", "SHY", "GOVT", "BIL", "SGOV",
    "HYG", "JNK", "LQD", "VCIT", "VCSH", "TIP", "MUB",
    # Dividend & Income ETFs
    "SCHD", "VYM", "VIG", "DGRO", "JEPI", "JEPQ", "NOBL", "HDV",
    # Sector SPDRs & Real Estate ETFs
    "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC", "VNQ",
    # Thematic, Industry, Biotech, Commodities & Leveraged ETFs
    "SMH", "SOXX", "XBI", "IBB", "KRE", "ARKK",
    "GLD", "SLV", "IAU", "USO", "UNG",
    "TQQQ", "SQQQ", "SOXL", "SOXS", "UPRO", "SPXU"
}

# Synonyms and brand names mapped to ticker symbols
ALIASES: dict[str, list[str]] = {
    # Tech giants & popular companies
    "google": ["GOOGL", "GOOG"],
    "alphabet": ["GOOGL", "GOOG"],
    "facebook": ["META"],
    "meta": ["META"],
    "instagram": ["META"],
    "apple": ["AAPL"],
    "microsoft": ["MSFT"],
    "windows": ["MSFT"],
    "nvidia": ["NVDA"],
    "amazon": ["AMZN"],
    "tesla": ["TSLA"],
    "elon": ["TSLA"],
    "palantir": ["PLTR"],
    "netflix": ["NFLX"],
    "berkshire": ["BRK.B", "BRK.A"],
    "buffett": ["BRK.B", "BRK.A"],
    "disney": ["DIS"],
    "walmart": ["WMT"],
    "costco": ["COST"],
    "coca-cola": ["KO"],
    "coca cola": ["KO"],
    "pepsi": ["PEP"],
    "pepsico": ["PEP"],
    "tsmc": ["TSM"],
    "taiwan semi": ["TSM"],
    "taiwan semiconductor": ["TSM"],
    "uber": ["UBER"],
    "coinbase": ["COIN"],
    "crypto": ["COIN", "MSTR", "IBIT"],
    "bitcoin": ["MSTR", "COIN", "IBIT"],
    "microstrategy": ["MSTR"],
    "snowflake": ["SNOW"],
    "crowdstrike": ["CRWD"],
    "broadcom": ["AVGO"],
    "jpmorgan": ["JPM", "JEPI", "JEPQ"],
    "goldman": ["GS"],
    "morgan stanley": ["MS"],
    "boeing": ["BA"],
    "lockheed": ["LMT"],

    # Major market indices & broad market ETFs
    "s&p": ["SPY", "IVV", "VOO", "SPLG"],
    "s&p 500": ["SPY", "IVV", "VOO", "SPLG"],
    "sp500": ["SPY", "IVV", "VOO", "SPLG"],
    "nasdaq": ["QQQ", "QQQM"],
    "nasdaq 100": ["QQQ", "QQQM"],
    "dow": ["DIA"],
    "dow jones": ["DIA"],
    "djia": ["DIA"],
    "russell": ["IWM", "IWF", "IWD"],
    "russell 2000": ["IWM"],
    "total market": ["VTI", "ITOT", "SCHB"],
    "international": ["VXUS", "VEA", "IEFA", "VT"],
    "emerging markets": ["VWO", "IEMG", "EEM"],
    "small cap": ["IWM", "IJR", "VB", "SCHA"],
    "small caps": ["IWM", "IJR", "VB", "SCHA"],
    "mid cap": ["IJH", "VO"],
    "growth": ["QQQ", "VUG", "IWF", "SCHG"],
    "value": ["VTV", "IWD", "SCHV"],

    # ETF providers & brand families
    "ishares": ["IVV", "IEFA", "IEMG", "IWM", "AGG", "TLT", "SOXX", "IJH", "IJR"],
    "vanguard": ["VOO", "VTI", "VXUS", "BND", "VUG", "VTV", "VEA", "VWO", "VNQ"],
    "invesco": ["QQQ", "QQQM", "RSP"],
    "spdr": ["SPY", "DIA", "XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC"],
    "schwab": ["SCHD", "SCHG", "SCHB", "SCHA"],
    "ark": ["ARKK"],
    "arkk": ["ARKK"],
    "cathie wood": ["ARKK"],

    # Dividends & Income
    "dividend": ["SCHD", "VYM", "JEPI", "VIG", "DGRO"],
    "dividends": ["SCHD", "VYM", "JEPI", "VIG", "DGRO"],
    "high yield": ["JEPI", "JEPQ", "HYG", "JNK", "SCHD"],

    # Fixed income & bonds
    "bonds": ["BND", "AGG", "TLT", "IEF", "SHY", "HYG", "LQD"],
    "bond": ["BND", "AGG", "TLT", "IEF", "SHY", "HYG", "LQD"],
    "treasury": ["TLT", "IEF", "SHY", "SGOV", "BIL", "GOVT"],
    "treasuries": ["TLT", "IEF", "SHY", "SGOV", "BIL", "GOVT"],

    # Sectors, Themes & Commodities
    "semiconductor": ["SMH", "SOXX", "NVDA", "TSM", "AMD"],
    "semiconductors": ["SMH", "SOXX", "NVDA", "TSM", "AMD"],
    "chips": ["NVDA", "AMD", "TSM", "INTC", "QCOM", "AVGO", "SMH"],
    "tech": ["XLK", "QQQ", "AAPL", "MSFT", "NVDA"],
    "technology": ["XLK", "QQQ", "AAPL", "MSFT", "NVDA"],
    "financials": ["XLF", "JPM", "BAC", "GS"],
    "financial": ["XLF", "JPM", "BAC", "GS"],
    "energy": ["XLE", "XOM", "CVX"],
    "oil": ["USO", "XLE", "XOM", "CVX"],
    "gas": ["UNG"],
    "gold": ["GLD", "IAU"],
    "silver": ["SLV"],
    "healthcare": ["XLV", "LLY", "UNH", "JNJ", "ABBV"],
    "health care": ["XLV", "LLY", "UNH", "JNJ", "ABBV"],
    "biotech": ["XBI", "IBB"],
    "communication": ["XLC", "META", "GOOGL", "DIS", "NFLX"],
    "communications": ["XLC", "META", "GOOGL", "DIS", "NFLX"],
    "real estate": ["VNQ", "XLRE"],
    "reit": ["VNQ", "XLRE"],
    "regional banks": ["KRE"],
    "utilities": ["XLU", "NEE"],
    "industrials": ["XLI", "CAT", "GE", "BA"],
    "materials": ["XLB", "LIN"],
    "consumer staples": ["XLP", "PG", "COST", "WMT", "KO", "PEP"],
    "consumer discretionary": ["XLY", "AMZN", "TSLA", "HD", "MCD", "NKE"],
}

# Fallback dataset if symbols.json is not present
FALLBACK_SYMBOLS: list[dict[str, str]] = [
    # Top individual stocks
    {"symbol": "AAPL", "name": "Apple Inc."},
    {"symbol": "MSFT", "name": "Microsoft Corp."},
    {"symbol": "NVDA", "name": "Nvidia Corp."},
    {"symbol": "GOOGL", "name": "Alphabet Inc. (Class A)"},
    {"symbol": "GOOG", "name": "Alphabet Inc. (Class C)"},
    {"symbol": "AMZN", "name": "Amazon.com Inc."},
    {"symbol": "META", "name": "Meta Platforms, Inc."},
    {"symbol": "TSLA", "name": "Tesla, Inc."},
    {"symbol": "BRK.B", "name": "Berkshire Hathaway Inc."},
    {"symbol": "LLY", "name": "Eli Lilly and Co."},
    {"symbol": "AVGO", "name": "Broadcom Inc."},
    {"symbol": "JPM", "name": "JPMorgan Chase & Co."},
    {"symbol": "V", "name": "Visa Inc."},
    {"symbol": "UNH", "name": "UnitedHealth Group Inc."},
    {"symbol": "XOM", "name": "Exxon Mobil Corp."},
    {"symbol": "MA", "name": "Mastercard Inc."},
    {"symbol": "JNJ", "name": "Johnson & Johnson"},
    {"symbol": "PG", "name": "Procter & Gamble Co."},
    {"symbol": "HD", "name": "Home Depot, Inc."},
    {"symbol": "COST", "name": "Costco Wholesale Corp."},
    {"symbol": "ABBV", "name": "AbbVie Inc."},
    {"symbol": "MRK", "name": "Merck & Co., Inc."},
    {"symbol": "CRM", "name": "Salesforce, Inc."},
    {"symbol": "BAC", "name": "Bank of America Corp."},
    {"symbol": "CVX", "name": "Chevron Corp."},
    {"symbol": "AMD", "name": "Advanced Micro Devices, Inc."},
    {"symbol": "NFLX", "name": "Netflix, Inc."},
    {"symbol": "WMT", "name": "Walmart Inc."},
    {"symbol": "PLTR", "name": "Palantir Technologies Inc."},
    {"symbol": "INTC", "name": "Intel Corp."},
    {"symbol": "DIS", "name": "The Walt Disney Company"},
    {"symbol": "UBER", "name": "Uber Technologies, Inc."},
    {"symbol": "TSM", "name": "Taiwan Semiconductor Manufacturing Co. Ltd."},

    # Core & Broad Market ETFs
    {"symbol": "IVV", "name": "iShares Core S&P 500 ETF"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust"},
    {"symbol": "VOO", "name": "Vanguard S&P 500 ETF"},
    {"symbol": "SPLG", "name": "SPDR Portfolio S&P 500 ETF"},
    {"symbol": "RSP", "name": "Invesco S&P 500 Equal Weight ETF"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust"},
    {"symbol": "QQQM", "name": "Invesco NASDAQ 100 ETF"},
    {"symbol": "DIA", "name": "SPDR Dow Jones Industrial Average ETF Trust"},
    {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF"},
    {"symbol": "ITOT", "name": "iShares Core S&P Total U.S. Stock Market ETF"},
    {"symbol": "SCHB", "name": "Schwab U.S. Broad Market ETF"},

    # Growth & Value ETFs
    {"symbol": "VUG", "name": "Vanguard Growth ETF"},
    {"symbol": "VTV", "name": "Vanguard Value ETF"},
    {"symbol": "IWF", "name": "iShares Russell 1000 Growth ETF"},
    {"symbol": "IWD", "name": "iShares Russell 1000 Value ETF"},
    {"symbol": "IWB", "name": "iShares Russell 1000 ETF"},
    {"symbol": "SCHG", "name": "Schwab U.S. Large-Cap Growth ETF"},
    {"symbol": "SCHV", "name": "Schwab U.S. Large-Cap Value ETF"},

    # Mid & Small Cap ETFs
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF"},
    {"symbol": "IJH", "name": "iShares Core S&P Mid-Cap ETF"},
    {"symbol": "IJR", "name": "iShares Core S&P Small-Cap ETF"},
    {"symbol": "VB", "name": "Vanguard Small-Cap ETF"},
    {"symbol": "VO", "name": "Vanguard Mid-Cap ETF"},
    {"symbol": "VXF", "name": "Vanguard Extended Market ETF"},
    {"symbol": "SCHA", "name": "Schwab U.S. Small-Cap ETF"},

    # International & Emerging Markets ETFs
    {"symbol": "VXUS", "name": "Vanguard Total International Stock ETF"},
    {"symbol": "VEA", "name": "Vanguard FTSE Developed Markets ETF"},
    {"symbol": "IEFA", "name": "iShares Core MSCI EAFE ETF"},
    {"symbol": "VWO", "name": "Vanguard FTSE Emerging Markets ETF"},
    {"symbol": "IEMG", "name": "iShares Core MSCI Emerging Markets ETF"},
    {"symbol": "VT", "name": "Vanguard Total World Stock ETF"},
    {"symbol": "EEM", "name": "iShares MSCI Emerging Markets ETF"},
    {"symbol": "EFA", "name": "iShares MSCI EAFE ETF"},
    {"symbol": "IXUS", "name": "iShares Core MSCI Total International Stock ETF"},
    {"symbol": "ACWI", "name": "iShares MSCI ACWI ETF"},

    # Bonds & Fixed Income ETFs
    {"symbol": "BND", "name": "Vanguard Total Bond Market ETF"},
    {"symbol": "AGG", "name": "iShares Core U.S. Aggregate Bond ETF"},
    {"symbol": "BNDX", "name": "Vanguard Total International Bond ETF"},
    {"symbol": "TLT", "name": "iShares 20+ Year Treasury Bond ETF"},
    {"symbol": "IEF", "name": "iShares 7-10 Year Treasury Bond ETF"},
    {"symbol": "SHY", "name": "iShares 1-3 Year Treasury Bond ETF"},
    {"symbol": "GOVT", "name": "iShares U.S. Treasury Bond ETF"},
    {"symbol": "BIL", "name": "SPDR Bloomberg 1-3 Month T-Bill ETF"},
    {"symbol": "SGOV", "name": "iShares 0-3 Month Treasury Bond ETF"},
    {"symbol": "HYG", "name": "iShares iBoxx $ High Yield Corporate Bond ETF"},
    {"symbol": "JNK", "name": "SPDR Bloomberg High Yield Bond ETF"},
    {"symbol": "LQD", "name": "iShares iBoxx $ Investment Grade Corporate Bond ETF"},
    {"symbol": "VCIT", "name": "Vanguard Intermediate-Term Corporate Bond ETF"},
    {"symbol": "VCSH", "name": "Vanguard Short-Term Corporate Bond ETF"},
    {"symbol": "TIP", "name": "iShares TIPS Bond ETF"},
    {"symbol": "MUB", "name": "iShares National Muni Bond ETF"},

    # Dividend & High-Yield ETFs
    {"symbol": "SCHD", "name": "Schwab U.S. Dividend Equity ETF"},
    {"symbol": "VYM", "name": "Vanguard High Dividend Yield ETF"},
    {"symbol": "VIG", "name": "Vanguard Dividend Appreciation ETF"},
    {"symbol": "DGRO", "name": "iShares Core Dividend Growth ETF"},
    {"symbol": "JEPI", "name": "JPMorgan Equity Premium Income ETF"},
    {"symbol": "JEPQ", "name": "JPMorgan Nasdaq Equity Premium Income ETF"},
    {"symbol": "NOBL", "name": "ProShares S&P 500 Dividend Aristocrats ETF"},
    {"symbol": "HDV", "name": "iShares Core High Dividend ETF"},

    # Sector SPDRs & Real Estate ETFs
    {"symbol": "XLK", "name": "Technology Select Sector SPDR Fund"},
    {"symbol": "XLF", "name": "Financial Select Sector SPDR Fund"},
    {"symbol": "XLV", "name": "Health Care Select Sector SPDR Fund"},
    {"symbol": "XLE", "name": "Energy Select Sector SPDR Fund"},
    {"symbol": "XLI", "name": "Industrial Select Sector SPDR Fund"},
    {"symbol": "XLP", "name": "Consumer Staples Select Sector SPDR Fund"},
    {"symbol": "XLY", "name": "Consumer Discretionary Select Sector SPDR Fund"},
    {"symbol": "XLU", "name": "Utilities Select Sector SPDR Fund"},
    {"symbol": "XLB", "name": "Materials Select Sector SPDR Fund"},
    {"symbol": "XLRE", "name": "Real Estate Select Sector SPDR Fund"},
    {"symbol": "XLC", "name": "Communication Services Select Sector SPDR Fund"},
    {"symbol": "VNQ", "name": "Vanguard Real Estate ETF"},

    # Thematic, Commodities & Leveraged ETFs
    {"symbol": "SMH", "name": "VanEck Semiconductor ETF"},
    {"symbol": "SOXX", "name": "iShares Semiconductor ETF"},
    {"symbol": "XBI", "name": "SPDR S&P Biotech ETF"},
    {"symbol": "IBB", "name": "iShares Biotechnology ETF"},
    {"symbol": "KRE", "name": "SPDR S&P Regional Banking ETF"},
    {"symbol": "ARKK", "name": "ARK Innovation ETF"},
    {"symbol": "GLD", "name": "SPDR Gold Shares"},
    {"symbol": "IAU", "name": "iShares Gold Trust"},
    {"symbol": "SLV", "name": "iShares Silver Trust"},
    {"symbol": "USO", "name": "United States Oil Fund"},
    {"symbol": "UNG", "name": "United States Natural Gas Fund, LP"},
    {"symbol": "TQQQ", "name": "ProShares UltraPro QQQ"},
    {"symbol": "SQQQ", "name": "ProShares UltraPro Short QQQ"},
    {"symbol": "SOXL", "name": "Direxion Daily Semiconductor Bull 3X Shares"},
    {"symbol": "SOXS", "name": "Direxion Daily Semiconductor Bear 3X Shares"},
    {"symbol": "UPRO", "name": "ProShares UltraPro S&P 500"},
    {"symbol": "SPXU", "name": "ProShares UltraPro Short S&P 500"},
]

_SYMBOLS_CACHE: list[dict[str, str]] | None = None
_SYMBOLS_MAP: dict[str, str] = {}


def clear_symbol_cache() -> None:
    """Clear symbols cache, forcing reload on next call."""
    global _SYMBOLS_CACHE, _SYMBOLS_MAP
    _SYMBOLS_CACHE = None
    _SYMBOLS_MAP = {}


def load_symbols() -> list[dict[str, str]]:
    """Load symbol dataset from symbols.json or fallback list."""
    global _SYMBOLS_CACHE, _SYMBOLS_MAP
    if _SYMBOLS_CACHE is not None:
        return _SYMBOLS_CACHE

    symbols = []
    if SYMBOLS_FILE.exists():
        try:
            with open(SYMBOLS_FILE, encoding="utf-8") as f:
                symbols = json.load(f)
        except Exception as e:
            logger.warning("Could not read %s, using fallback symbols: %s", SYMBOLS_FILE, e)

    if not symbols:
        symbols = list(FALLBACK_SYMBOLS)
    else:
        # Guarantee all curated fallback symbols (including IVV and major ETFs) are present
        existing_tickers = {s["symbol"].upper() for s in symbols}
        for item in FALLBACK_SYMBOLS:
            if item["symbol"].upper() not in existing_tickers:
                symbols.append(item)
                existing_tickers.add(item["symbol"].upper())

    _SYMBOLS_CACHE = symbols
    _SYMBOLS_MAP = {s["symbol"].upper(): s["name"] for s in symbols}
    return _SYMBOLS_CACHE


def get_symbol_map() -> dict[str, str]:
    """Return dictionary mapping uppercase symbol to company name."""
    if _SYMBOLS_CACHE is None:
        load_symbols()
    return _SYMBOLS_MAP


def get_symbol_info(symbol: str) -> dict[str, str] | None:
    """Lookup info for a specific ticker symbol."""
    sym_map = get_symbol_map()
    sym_upper = symbol.strip().upper()
    if sym_upper in sym_map:
        return {"symbol": sym_upper, "name": sym_map[sym_upper]}
    return None


def search_symbols(
    query: str,
    limit: int = 10,
    include_watchlist: bool = True,
) -> list[dict[str, str]]:
    """Search for symbols matching a partial ticker or company name.

    Results are ranked in prioritized tiers:
    1. Exact ticker symbol match (e.g. 'AAPL' -> AAPL)
    2. Ticker prefix match (e.g. 'AA' -> AAPL, AAL), with popular stocks prioritized
    3. Alias/Brand match (e.g. 'google' -> GOOGL, 'facebook' -> META)
    4. Company name prefix match (e.g. 'Apple' -> Apple Inc.)
    5. Company name word prefix match (e.g. 'Tech' -> Palantir Technologies)
    6. Ticker substring match
    7. Company name substring match

    Returns a list of dicts with 'symbol' and 'name' keys.
    """
    q = query.strip()
    if not q:
        return []

    q_lower = q.lower()
    q_upper = q.upper()

    all_symbols = load_symbols()
    sym_map = get_symbol_map()

    # Dynamically inject current watchlist symbols if available
    extra_items: list[dict[str, str]] = []
    if include_watchlist:
        try:
            from webapp.db import get_watchlist
            for item in get_watchlist():
                s = item.get("symbol", "").strip().upper()
                if s and s not in sym_map:
                    extra_items.append({
                        "symbol": s,
                        "name": item.get("notes") or f"{s} (Watchlist)",
                    })
        except Exception:
            pass

    dataset = extra_items + all_symbols

    exact_matches: list[dict[str, str]] = []
    symbol_prefix_matches: list[dict[str, str]] = []
    alias_matches: list[dict[str, str]] = []
    name_prefix_matches: list[dict[str, str]] = []
    name_word_prefix_matches: list[dict[str, str]] = []
    symbol_contains_matches: list[dict[str, str]] = []
    name_contains_matches: list[dict[str, str]] = []

    seen: set[str] = set()

    # Check aliases (exact match or prefix match if query has >= 3 chars)
    for alias_key, alias_symbols in ALIASES.items():
        if alias_key == q_lower or (len(q_lower) >= 3 and alias_key.startswith(q_lower)):
            for sym in alias_symbols:
                sym_u = sym.upper()
                if sym_u not in seen:
                    name = sym_map.get(sym_u, sym_u)
                    alias_matches.append({"symbol": sym_u, "name": name})
                    seen.add(sym_u)

    for item in dataset:
        sym = item["symbol"]
        sym_u = sym.upper()
        if sym_u in seen:
            continue

        name = item.get("name", "")
        name_lower = name.lower()

        # 1. Exact symbol match
        if sym_u == q_upper:
            exact_matches.append({"symbol": sym_u, "name": name})
            seen.add(sym_u)
            continue

        # 2. Symbol starts with query
        if sym_u.startswith(q_upper):
            symbol_prefix_matches.append({"symbol": sym_u, "name": name})
            seen.add(sym_u)
            continue

        # 4. Company name starts with query
        if name_lower.startswith(q_lower):
            name_prefix_matches.append({"symbol": sym_u, "name": name})
            seen.add(sym_u)
            continue

        # 5. Company name word prefix match
        words = name_lower.split()
        if any(w.startswith(q_lower) for w in words):
            name_word_prefix_matches.append({"symbol": sym_u, "name": name})
            seen.add(sym_u)
            continue

        # 6. Symbol contains query
        if q_upper in sym_u:
            symbol_contains_matches.append({"symbol": sym_u, "name": name})
            seen.add(sym_u)
            continue

        # 7. Company name contains query
        if q_lower in name_lower:
            name_contains_matches.append({"symbol": sym_u, "name": name})
            seen.add(sym_u)
            continue

    # Sort each tier intelligently
    def sort_key(x: dict[str, str]) -> tuple[int, int, str]:
        sym = x["symbol"]
        is_pop = 0 if sym in POPULAR_TICKERS else 1
        return (is_pop, len(sym), sym)

    symbol_prefix_matches.sort(key=sort_key)
    name_prefix_matches.sort(key=lambda x: (0 if x["symbol"] in POPULAR_TICKERS else 1, len(x["name"]), x["symbol"]))
    name_word_prefix_matches.sort(key=lambda x: (0 if x["symbol"] in POPULAR_TICKERS else 1, len(x["name"]), x["symbol"]))
    symbol_contains_matches.sort(key=sort_key)
    name_contains_matches.sort(key=lambda x: (0 if x["symbol"] in POPULAR_TICKERS else 1, len(x["name"]), x["symbol"]))

    results = (
        exact_matches
        + alias_matches
        + symbol_prefix_matches
        + name_prefix_matches
        + name_word_prefix_matches
        + symbol_contains_matches
        + name_contains_matches
    )

    return results[:limit]
