# TradingAgents Web App — Dashboard

A self-contained web application that wraps the TradingAgents framework for a hands-off, monitor-style algorithmic trading workflow with Alpaca (Paper/Live) integration.

---

## Features

1. **Watchlist Management & Periodic Analysis**
   - Configure stock and ETF tickers to track.
   - Run analysis on demand ("Run Now" per ticker or "Run All Active").
   - Multi-agent consensus analysis factors in your **current Alpaca positions** so agents know what is already held.
2. **Auto-Trade Toggle**
   - Safe advisory mode by default: recommendations are generated without placing orders.
   - Opt-in toggle enables non-blocking, automated submission of bracket/market/limit orders to Alpaca.
   - Respects `ALPACA_PAPER=true` (defaults to paper trading).
3. **Full Run History & Live Logs**
   - Background worker processes long-running analysis jobs without blocking HTTP handlers.
   - Real-time log streaming viewer in the browser shows agent thoughts, debate rounds, and decision steps.
   - Complete record of recommendations, entry/target/stop prices, position sizing, and executed order IDs (or skip reasons).
4. **Duplicate Run Protection**
   - In-flight concurrency lock prevents duplicate simultaneous runs for the same ticker.
5. **Zero-Build Static Frontend**
   - Pure vanilla JavaScript, CSS, and HTML served directly by FastAPI. No Node.js or frontend build steps required.

---

## Directory Structure

```
webapp/
├── __init__.py
├── app.py              # Application entrypoint (starts FastAPI & scheduler)
├── config.py           # Configuration & environment variable loading
├── db.py               # SQLite storage (under webapp/data/)
├── execution.py        # Shared execution bridge (reused by CLI execute_order.py)
├── runner.py           # Background worker & in-flight ticker concurrency guard
├── scheduler.py        # Periodic interval scheduler daemon
├── api.py              # REST API endpoints
├── data/               # Local SQLite database directory (gitignored)
│   └── trading_dashboard.db
└── static/             # Static UI assets
    ├── index.html      # Single Page Application
    ├── style.css       # Dark-mode styling
    └── app.js          # Interactive dashboard controller
```

---

## Quick Start

### 1. Prerequisites
Ensure project virtual environment has dependencies installed:
```bash
.venv/bin/python -m pip install -r requirements.txt
```

### 2. Configure Environment (`.env`)
Verify `.env` in the project root has your credentials:
```env
ALPACA_API_KEY=your_key_here
ALPACA_SECRET_KEY=your_secret_here
ALPACA_PAPER=true
TRADINGAGENTS_LLM_PROVIDER=openai_compatible
TRADINGAGENTS_LLM_BACKEND_URL=http://.../v1
TRADINGAGENTS_DEEP_THINK_LLM=qwen38
TRADINGAGENTS_QUICK_THINK_LLM=qwen38
```

### 3. Start the Web App
Run with the project virtual environment Python:
```bash
.venv/bin/python -m webapp.app --port 8037
```
Or directly with Uvicorn:
```bash
.venv/bin/python -m uvicorn webapp.app:app --host 0.0.0.0 --port 8037
```

Open your browser to:
[http://localhost:8037](http://localhost:8037)

---

## CLI Compatibility (`execute_order.py`)

The CLI execution script `execute_order.py` shares the exact same execution module (`webapp.execution`) and remains 100% backward-compatible:

```bash
# Auto-execute single ticker via CLI:
.venv/bin/python execute_order.py NVDA

# Dry run (show order without placing):
.venv/bin/python execute_order.py NVDA --dry-run

# Replay from a saved state log:
.venv/bin/python execute_order.py NVDA --from-log
```

---

## REST API Overview

| Endpoint | Method | Description |
|---|---|---|
| `/api/health` | GET | Healthcheck |
| `/api/account` | GET | Alpaca account equity, cash, paper flag, and open positions |
| `/api/positions` | GET | Open positions list |
| `/api/watchlist` | GET / POST | View or add watchlist symbols |
| `/api/watchlist/{symbol}` | DELETE / PATCH | Remove or toggle active state of a symbol |
| `/api/settings` | GET / POST | View or update auto-trade toggle and scheduler interval |
| `/api/runs` | GET / POST | List run history or trigger a new analysis |
| `/api/runs/in-flight` | GET | List tickers currently being analyzed |
| `/api/runs/{run_id}` | GET | Fetch details for a specific run |
| `/api/runs/{run_id}/logs` | GET | Stream raw terminal logs for a run |
| `/api/recommendations` | GET | List structured recommendations |
| `/api/orders` | GET | List submitted or skipped order records |
