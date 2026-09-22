# Requirement: Buy/Sell decisions must respect the caller's current position

## Problem
The dashboard recommended SELL GOOG (123 shares) even though the caller holds 0 shares of
GOOG (account is flat). The analysis log correctly detected "No existing position held in
GOOG (Flat)", yet the decision card still shows a SELL order. Root causes:

1. The Trader prompt has a fixed mapping "Underweight is a Sell" (and "Overweight is a Buy"),
   so a bearish view is always translated into a SELL action regardless of whether the caller
   actually holds the stock. When flat, "bearish / underweight" really means "avoid entry",
   not "sell what you hold".

2. The order builder (webapp/execution.py `build_order`) does not know about current holdings.
   The position-aware sell guard (cannot sell what you don't hold) currently only exists in
   `runner._execute_job` and in `execute_recommendation`, which only run when auto-trade is ON.
   In Advisory mode (auto-trade OFF) no guard runs, so a flat SELL decision is shown as-is.

## Goal
A SELL order (or SELL wording) should only ever be produced for a ticker the caller actually
holds. When the caller is flat, a bearish/underweight view should surface as "avoid entry /
no action" rather than "sell X shares".

## Changes (keep it minimal and consistent with existing code)

1. **Agents (position-aware wording).** In `tradingagents/agents/trader/trader.py` the Trader
   prompt hard-codes "Overweight is a Buy and Underweight is a Sell". Make this conditional on
   the caller's position, which is already in the prompt via `portfolio_context`
   (`No current position in {SYM}` vs `Current position in {SYM}: N units`).
   - If the caller has NO position in this ticker: a bearish view (Underweight / Sell) should be
     expressed as "avoid entry / do not open a new position" and the Action should be **Hold**
     (not Sell). A bullish view maps to Buy as before.
   - If the caller HAS a position: keep the existing Buy/Sell mapping (Underweight → Sell, etc.).
   Keep it in the prompt/system text (this is an LLM prompt, not code logic). Do the same kind
   of position-awareness in `tradingagents/agents/managers/portfolio_manager.py` rating guidance
   if useful, but the Trader Action is what drives orders, so the Trader prompt is the priority.

2. **Order builder guard.** In `webapp/execution.py`, add a `held_qty: float | None = None`
   parameter to `build_order()`. After the direction is determined, if direction is `sell` and
   the caller holds no position (`held_qty is None or held_qty <= 0`), return `None` (no order).
   This makes the guard apply everywhere `build_order` is used, including manual/confirm flows,
   not just the auto-trade path. Keep the existing `min(qty, held_qty)` clamping for the case
   where the caller holds fewer shares than requested.

3. **Wiring.** In `webapp/runner.py`, pass the already-computed `held_qty` into `build_order(...)`.
   Where a SELL is skipped because of a flat position, log a clear line such as:
   "⏸️ Skipped SELL order: you do not hold {TICKER} (0 shares) — bearish view means avoid entry,
   no position to close."

4. **UI wording.** In `webapp/static/app.js` (and any template), when the decision Action is
   SELL but the stored `current_position_qty` is 0 / null, render the action label as
   "Avoid Entry (no position)" instead of "Sell", so the card is not misleading. Keep the
   bilingual (en/zh) handling consistent with how other labels are rendered.

## Verification
- Add/extend tests in `tests/`:
  - `build_order(ticker, decision_with_sell, equity, held_qty=0)` returns `None`.
  - `build_order(..., held_qty=10)` for a sell whose qty would be 50 clamps to 10.
  - Trader prompt text includes position-conditional wording (a light assertion that the flat
    branch says to avoid opening / use Hold when no position).
- Run the full suite: `.venv/bin/pytest -q` — must pass.
- Bump the frontend cache-busting version in `webapp/static/app.js` if a new JS file/asset is added.

Do NOT start a manual uvicorn on port 8037 — the dashboard is owned by systemd
`trading-dashboard.service`. Just edit code + run pytest; I will restart the service.
