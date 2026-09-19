# Web App Spec — "Trading Agent Dashboard"

Build a self-contained web application in this repo (a new `webapp/` directory) that wraps the
existing TradingAgents framework for a hands-off, monitor-style workflow.

## Goals
1. **Watchlist + periodic analysis.** The user configures a list of stocks/ETFs. The app
   periodically runs the TradingAgents pipeline for each ticker and produces a trading
   recommendation for each (action, rationale, entry/stop/target, position sizing, rating).
   Recommendations must factor in the user's **current positions** (from Alpaca) so the user can
   see "I already own X of this."
2. **Auto-trade toggle.** The user can flip on an "auto-trade" mode. When enabled, following
   executions are submitted automatically to the Alpaca account (paper by default; respect the
   existing `ALPACA_PAPER` env var). When off, recommendations are advisory only.
3. **Full logs.** The UI shows the complete history of every periodic run: each recommendation,
   its timestamp, the reasoning the agents produced, and — if auto-trade was on — what order was
   submitted (or why it was skipped).
4. **Scheduling.** The app runs analysis on a configurable interval (default e.g. daily) in the
   background, and also supports a "run now" button.

## Constraints / hints (high-level — explore the repo for details)
- Python. There is already an `.env` at repo root with all the keys (LLM backend, Alpaca, FRED).
  Load it with `python-dotenv` (already a dependency).
- There is an existing `execute_order.py` at repo root that already (a) runs TradingAgents for a
  single ticker, (b) parses the final decision from the graph state, and (c) submits a bracket
  order to Alpaca. **Reuse its logic** rather than forking it** — import or refactor it into a shared
  helper module so the web app and the CLI script share one code path. Keep `execute_order.py`
  working as-is (backward compatible).
- The LLM backend is a custom vLLM endpoint (`openai_compatible`, model `qwen38`). A full
  multi-agent run is SLOW (tens of minutes per ticker). So the web app must:
  - Run analysis jobs in a **background worker** (thread/process), NOT blocking the HTTP request
    handlers.
  - Stream/append to logs as work progresses so the UI can show live progress.
  - Make the watchlist run concurrent or at least non-blocking, and be re-entrant-safe (don't
    start a second full run of the same ticker while one is in flight).
- Storage: keep it simple and local. A SQLite database (or plain JSON files under `webapp/data/`)
  is fine for: watchlist config, run history, recommendations, and submitted orders. Do not add a
  separate DB server.
- The web framework is your choice (FastAPI + a lightweight JS frontend is reasonable). The
  frontend should be plain static files served by the app (no build step / no node required to
  run) so it works with just the Python venv.
- Read current Alpaca positions via the existing keys for the "current position" context.

## Deliverables
- `webapp/` package: an app entrypoint that starts the web server and the background scheduler.
- A small set of modules (config/storage, the analysis runner reusing execute_order logic, and the
  web API + static frontend).
- A README section or `webapp/README.md` explaining how to run it and what each page does.
- Add any new runtime deps needed (e.g. `fastapi`, `uvicorn`) to `requirements.txt` /
  `pyproject.toml`.
- Do NOT commit secrets. `.env` must stay gitignored (verify `.gitignore` covers it).

## Definition of done
- `python -m webapp.app` (or similar) starts a server on a configurable port.
- The UI lets the user: view/edit the watchlist, see current Alpaca positions, run analysis now,
  see full run logs/recommendations with reasons, and toggle auto-trade on/off.
- A live (non-dry-run) auto-trade execution path exists and is gated behind the toggle, defaulting
  to paper trading.
- Nothing in the committed tree contains the real API keys.
