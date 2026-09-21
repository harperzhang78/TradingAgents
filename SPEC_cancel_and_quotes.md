# SPEC: Cancel pending order + show current price & daily %

Two self-contained enhancements to the trading dashboard webapp (FastAPI + vanilla JS).
Keep edits targeted and consistent with existing code style. Do NOT rewrite whole files.

## Background (already present — build on top, don't duplicate)
- `webapp/execution.py` already has:
  - `fetch_last_close_price(ticker)` — returns the latest trade price (float | None) via Alpaca data API.
  - `get_live_orders(client)` / `get_live_order(order_id)` / `cancel_live_order(order_id)` — Alpaca order helpers.
- `webapp/api.py` already has:
  - `GET /api/orders/live` → list of open orders (from Alpaca).
  - `GET /api/orders/live/{order_id}`.
  - `POST /api/orders/live/{order_id}/cancel` and `DELETE /api/orders/live/{order_id}` → call `cancel_live_order(order_id)`, return `{"success": True, ...}`.
- `webapp/static/app.js` already has:
  - `renderPositions()` —> uses `state.account.positions` (each position has `symbol, qty, avg_entry_price, current_price, market_value, unrealized_pl, unrealized_plpc`).
  - `renderWatchlist()` → renders `#watchlist-tbody` rows for each watchlist symbol.
  - `renderLiveOrders()` → renders the "Active Orders" card `#orders-tbody` and already has a Cancel button per row that calls `cancelLiveOrder(orderId)`.
  - `cancelLiveOrder(orderId)` → POSTs `/api/orders/live/{orderId}/cancel`, shows a toast.
- `webapp/db.py` `orders` table has `alpaca_order_id`, `client_order_id`, `status`, `raw_response`.
- `get_order_by_run(run_id)` returns the order row for a run (with `status` and `alpaca_order_id`).
- Frontend run cards are rendered by the function that builds `.run-item-card` (search for `orderBoxHtml`). For a `submitted` order it shows a "SUBMITTED" badge + a "⚡ Re-execute" button.
- Cache-busting is `?v=20260921_v5` in BOTH `webapp/templates/index.html` and `webapp/static/index.html` for `style.css` and `app.js`.

## Feature 1 — Cancel a pending (submitted/open) order
Goal: the user can cancel an order that was submitted to Alpaca but is not yet filled, straight from the
run card (and it should also be reflected in the Active Orders panel).

Backend:
- Add `POST /api/runs/{run_id}/cancel` (also alias `POST /api/orders/cancel` is optional) that:
  1. Loads the run and its order via `get_order_by_run(run_id)`.
  2. If no order, or `status` is not `submitted`, or there is no `alpaca_order_id` → return
     `HTTP 400` with a clear detail (e.g. "No cancelable (open) order for this run").
  3. Fetch the live order state via `get_live_order(alpaca_order_id)`. If Alpaca reports the order is no
     longer open (status in `{'filled','cancelled','canceled','expired','done','rejected'}`) → return 400
     "Order is no longer open; nothing to cancel." (idempotent-safe).
  4. Otherwise call `cancel_live_order(alpaca_order_id)`.
  5. On success, update the DB order row for the run: set `status = 'cancelled'` (use the existing
     `record_order_execution` or a small `update_order_status` helper if needed) and append a run log line
     like `[HH:MM:SS] 🚫 Order cancelled on Alpaca (Order ID: ...)`.
  6. Return `{"success": True, "run_id": run_id, "alpaca_order_id": ..., "status": "cancelled"}`.
  7. Wrap Alpaca errors into `HTTP 502` with a friendly detail.
- Keep it simple and robust; a brand-new order that is only `advisory`/`skipped`/`failed` must NOT be cancellable.

Frontend:
- In the run card, when `order.status === 'submitted'` AND `order.alpaca_order_id` exists, render a
  **Cancel** button (e.g. `btn-danger btn-sm`, label "✕ Cancel Order") next to the SUBMITTED badge /
  Re-execute button. It should call a new `cancelRunOrder(runId)` function.
- Add `async function cancelRunOrder(runId)` in `app.js`: confirm() ("Cancel this order on Alpaca? This
  cannot be undone."), POST `/api/runs/{runId}/cancel`, on success show a success toast and re-run the
  existing data refresh (the same one used after add/delete watchlist or after execute — reuse it, e.g.
  `loadAll()` / whatever reloads runs + positions + live orders), on error show an error toast with the
  backend message.
- Expose `window.cancelRunOrder = cancelRunOrder;` like the other handlers.
- The Active Orders panel already supports cancel per open order; leave it as-is (it covers the same action).

## Feature 2 — Show current price & daily change % for each stock
Goal: each watchlist symbol shows its **current price** and **daily change %** (today vs previous close).

Backend:
- Add a helper in `webapp/execution.py`, e.g. `get_stock_quote(symbol) -> dict | None`, that returns:
  `{"symbol": ..., "current_price": float, "prev_close": float, "change_pct": float}`.
  - `current_price` = latest trade/bar price (reuse the same Alpaca data client pattern as
    `fetch_last_close_price`).
  - `prev_close` = the PREVIOUS trading day's close. Use the Alpaca data client
    (`StockHistoricalDataClient` with `get_stock_bars` / `StockBarsRequest`) to fetch the last ~3 trading
    bars and take the close of the bar immediately before the latest (i.e. previous session close). If a
    previous close is not available (e.g. brand-new symbol, single bar), set `prev_close = None`.
  - `change_pct = (current_price - prev_close) / prev_close * 100` when `prev_close` is truthy, else `None`.
  - Must be resilient: wrap in try/except, return `None` (or a dict with `current_price` only) on any API
    error — never crash the page load.
- Add `GET /api/stocks/quotes?symbols=AAPL,MSFT` (comma-separated, optional query param; default = the
  current watchlist symbols if the param is empty/absent). Returns a dict mapping symbol → quote object
  (symbols that fail are simply absent or have `current_price`/`prev_close` null). Batch-cap at ~40
  symbols. This is the single source the frontend polls.

Frontend:
- In `renderWatchlist()`, add **Price** and **Chg %** to each row (and add the two columns to the
  watchlist `<thead>` in BOTH `webapp/templates/index.html` and `webapp/static/index.html`).
  - Price shows `state.stockQuotes[sym]?.current_price` formatted with `formatCurrency`, or "—" when
    missing.
  - Chg % shows a signed, color-coded pill: green `▲ +x.x%` when ≥0, red `▼ -x.x%` when <0, neutral
    "—" when unavailable. Use `state.stockQuotes[sym]?.change_pct`.
  - Update the `<td colspan=...>` counts in the empty/loading rows to match the new column count.
- Add a `state.stockQuotes = {}` (symbol→quote) and a `loadStockQuotes()` that calls
  `GET /api/stocks/quotes` (all watchlist symbols) and updates `state.stockQuotes`, then re-renders the
  watchlist. Call it on initial load alongside watchlist/account loads, and on the same refresh cadence
  the app already polls (so prices stay reasonably fresh). Make it non-blocking/optional — a failure to
  fetch quotes must not break the page.
- Add a small CSS class or reuse existing `.text-success`/`.text-danger` for the change color (keep it
  subtle, text-dim when unknown). A tiny `.chip-change` / `.change-up` / `.change-down`
  style in `style.css` is fine.

## Cache-busting
- Bump `?v=20260921_v5` → `?v=20260921_v6` for BOTH `style.css` and `app.js` in BOTH
  `webapp/templates/index.html` and `webapp/static/index.html`.

## Definition of done
1. Full suite green: `cd ~/Projects/TradingAgents && .venv/bin/pytest -q` (baseline 1055 passed, 2
   skipped — you may add a few more).
2. Add/extend tests:
   - Cancel endpoint: a submitted order with a mocked live order that is still open → returns success and
     sets DB status `cancelled`; an order not open → 400; no order → 400.
   - Quotes endpoint: with a mocked Alpaca data client returning 2 bars → returns correct
     `current_price`/`prev_close`/`change_pct`; with 1 bar → `prev_close`/`change_pct` null; on API error
     → returns empty/None without raising.
   - Keep tests isolated (use the existing `isolate_test_db` / monkeypatch patterns; mock the Alpaca
     clients so no real network calls run).
3. App boots cleanly; no JS/CSS syntax errors (node --check the JS if possible).
4. Run cards for a submitted order show a Cancel button; watchlist rows show Price + colored Chg %.

Report a concise summary of exact edits (files + what changed) when done.
