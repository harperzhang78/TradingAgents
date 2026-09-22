# SPEC: Remove sizing from decision card + position validation check for sell orders

Two fixes to the trading dashboard webapp & execution engine (FastAPI + vanilla JS).

## Part 1 — Remove sizing from the decision card in the UI
Goal: Remove position size / quantity displays and text from the decision card / metrics section in the UI.

Files to check:
- `webapp/static/app.js`: Look for rendering of metrics or decision cards (e.g., `metricsHtml`, position sizing display inside decision cards or recommendation cards).
- `webapp/templates/index.html` / `webapp/static/index.html`: Check if any static templates render sizing inside decision cards.
- Remove any reference to `Position Sizing:` or `Position Size` in the decision card rendering (`metricsHtml` or `decision` card HTML in `app.js`). Keep execution modal sizing input (users still need to override quantity when executing if desired, but the agent's recommended sizing display should be removed from the decision card).

## Part 2 — Prevent selling unheld stocks (or validate position before selling)
Investigation context: Why was selling 50 shares of GOOG allowed when holding 0 shares of GOOG?
- In `webapp/execution.py`, `execute_recommendation()` fetches account overview (`get_account_overview()`), which includes `positions`.
- However, when building orders or executing recommendations (`build_order` or `execute_recommendation`), **there is currently no check verifying whether the user actually holds a long position in the stock when attempting a SELL order** (or whether short selling / selling flat stocks is allowed/disallowed).
- Alpaca paper accounts allow short selling by default (or opening short positions), BUT TradingAgents expects to liquidate or reduce existing holdings on a SELL recommendation, OR if the user is flat (0 shares), selling 50 shares opens an unhedged short position (or gets rejected if shorting isn't desired).
- Requirement:
  1. In `webapp/execution.py`, inside `execute_recommendation()` (and/or `build_order()` / validation guards), check the account's open positions for the target symbol.
  2. If `side == 'sell'`:
     - Check if the symbol is currently held in Alpaca positions (`get_account_overview()` or `client.get_all_positions()`).
     - If the user holds 0 shares (flat) or fewer shares than the requested order quantity, should we block it or adjust quantity?
     - Wait, let's check user intent: "Why I still let me sell GOOG 50 stocks even I don’t have any GOOG in my position? Is the sizing really looking at my current position?"
     - If a recommendation says SELL 50 GOOG, but user has 0 GOOG shares, executing a SELL order submits a short sale of 50 shares to Alpaca. But users often want a guard: **You cannot sell a stock you do not hold (or warn/block shorting unless explicitly intended)**, OR limit the sell quantity to currently owned shares (e.g. if you own 10 shares, you can sell at most 10 shares, or if you own 0 shares, block the sell order with a clear error: `Cannot sell {symbol}: you hold 0 shares (short selling is not permitted or requires holding position)`).
     - Let's implement a robust position validation check in `execute_recommendation()`:
       - Fetch current positions from Alpaca. Find position for `ticker`.
       - If `side == 'sell'`:
         - If position qty is <= 0 (or position does not exist), raise `ValueError(f"Cannot sell {ticker}: you do not hold any position in {ticker} (current shares: 0)")`.
         - If position qty > 0, ensure the order quantity does not exceed the currently held shares (`qty = min(requested_qty, held_qty)` or raise/warn if trying to sell more than owned). Let's clamp or restrict sell quantity to available shares (`held_qty`), or block if flat. Clamping `qty = min(order_spec['qty'], held_qty)` or raising a ValueError "Cannot sell {qty} shares of {ticker}: you only own {held_qty} shares" is extremely safe and prevents accidental short selling!
         - Let's check what the user asked: "Why I still let me sell GOOG 50 stocks even I don’t have any GOOG in my position? Is the sizing really looking at my current position?"
         - This confirms the user expects **you cannot sell a stock you don't hold**, or at least that selling should be constrained by current holdings.
         - Therefore, when `side == 'sell'`:
           1. If held quantity <= 0 (or not held), raise `ValueError(f"Cannot sell {ticker}: you do not hold a position in {ticker}")`.
           2. If held quantity > 0, ensure `qty` does not exceed `held_qty` (e.g., `qty = min(order_spec['qty'], int(held_qty))`).

## Tests & Verification
- Add unit tests in `tests/` covering:
  - Attempting to execute a SELL order for a ticker with 0 shares in position raises ValueError ("Cannot sell... you do not hold a position").
  - Decision card rendering does not include position sizing text/badge.
- Run full pytest suite (`.venv/bin/pytest -q`).
- Bump cache-busting version in index.html (`?v=20260921_v7`).
- Restart service, commit & push.
