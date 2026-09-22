# Requirement: Market Order Default + 1-Week Short-Term Focus

## 1. Trader: Default to Market Order

**Problem**: The Trader LLM currently always tries to give an `entry_price`, which results in LIMIT orders. In most cases, the user just wants to trade at market price. LIMIT orders only add slippage risk and may not fill.

**Change**: Modify the Trader prompt (in `tradingagents/agents/trader/trader.py`) so that:

- The DEFAULT behavior is to NOT provide an entry price (use market order).
- Only provide an `entry_price` if the LLM identifies a **significantly better entry level** (e.g., waiting for a pullback to a key support level that is >5% below current price).
- Keep the `stop_loss` field as-is (still useful).
- Update the system prompt wording accordingly: instead of "State entry price and stop-loss as absolute price levels", say something like:
  - "By default, do NOT provide an entry price — the execution engine will use a market order. Only provide an entry price if the technical report reveals a significantly better level (e.g., a major support line 5%+ below current price) where you want to limit the entry. If you provide an entry price, it must be an absolute number in the instrument's quote currency."

**Also update** the `schemas.py` `TraderProposal.entry_price` field description to reflect this: "Optional. Only provide if you want to wait for a significantly better price (e.g., a support level 5%+ below current). Omit for market-order execution (the default)."

**No backend changes needed** — `build_order()` already handles `entry_price is None` by creating a MARKET order.

## 2. One-Week Short-Term Focus (All Agents)

**Goal**: The user's trading strategy is short-term (1 week / 5 trading days). All agents should frame their analysis and recommendations around this horizon. The expectation is to capture profit within one week.

**Changes per agent** (add a brief 1-2 sentence instruction, not a whole section):

### 2.1 Market Analyst (`analysts/market_analyst.py`)
Add to the system_message:
```
Your analysis should focus on the most recent 1-week (5 trading days) price action. Prioritize short-term momentum, recent crossovers, and near-term support/resistance levels relevant to a 1-week holding period.
```

### 2.2 Sentiment Analyst (`analysts/sentiment_analyst.py`)
The lookback window is already 7 days. Add to `_build_system_message`:
```
The trader's time horizon is 1 week. Frame your sentiment assessment as a near-term signal: is retail/institutional sentiment likely to support a price move within the next 5 trading days?
```

### 2.3 News Analyst (`analysts/news_analyst.py`)
Add to the system_message:
```
Focus on news and macro developments from the past week that are likely to impact the stock within the next 5 trading days. Prioritize imminent catalysts (earnings dates, product announcements, regulatory decisions) over long-term structural narratives.
```

### 2.4 Fundamentals Analyst (`analysts/fundamentals_analyst.py`)
Add to the system_message:
```
While providing a full fundamental picture, emphasize the most recent quarter's performance and any very recent (past 2 weeks) fundamental changes that could impact the stock within a 1-week horizon.
```

### 2.5 Bull Researcher (`researchers/bull_researcher.py`)
Add near the top of the prompt:
```
Time horizon: The trader plans to hold for approximately 1 week. Frame your bull case around catalysts and momentum that can materialize within 5 trading days.
```

### 2.6 Bear Researcher (`researchers/bear_researcher.py`)
Same addition:
```
Time horizon: The trader plans to hold for approximately 1 week. Frame your bear case around near-term risks, overhead resistance, and catalysts that could hit within 5 trading days.
```

### 2.7 Research Manager (`managers/research_manager.py`)
Add to the prompt (after the rating scale):
```
Time horizon: This is a short-term (1 week) trading decision. The strategic actions should be actionable within 5 trading days.
```

### 2.8 Trader (`agents/trader/trader.py`)
Add to the system message:
```
This is a short-term (1 week) trade. The trader expects to close the position within 5 trading days. Size the position and set the stop-loss accordingly — tight enough for a 1-week hold.
```

### 2.9 Portfolio Manager (`managers/portfolio_manager.py`)
Add to the prompt:
```
Time horizon: 1 week (5 trading days). The investment thesis and executive summary should be framed for a short-term trade expected to resolve within a week.
```

### 2.10 Risk Debaters (Aggressive/Conservative/Neutral)
Each should get:
```
Time horizon: This is a 1-week trade. Evaluate risk/reward on that timeframe, not a multi-month investment horizon.
```

## 3. Update `default_config.py`

Change the default `holding_period_days` from 5 to 5 (it's already 5, so no change needed — this is just confirming it aligns).

Actually no change needed here since it's already 5.

## 4. Tests

Update or add tests in `tests/` that verify:
- The Trader prompt contains the market-order-default language
- The prompts contain "1 week" or "5 trading days" references
- `build_order` still correctly creates a MARKET order when `entry_price` is None

Run the full test suite to confirm nothing breaks.
