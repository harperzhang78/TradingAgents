"""Background analysis runner with in-flight concurrency guards and live log streaming."""
from __future__ import annotations

import io
import json
import logging
import sys
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from webapp.config import PROJECT_ROOT
from webapp.db import (
    append_run_log,
    create_order_record,
    create_recommendation,
    create_run,
    get_active_watchlist,
    insert_llm_calls,
    is_auto_trade_enabled,
    update_run_status,
)
from tradingagents.llm_clients.llm_trace import (
    get_captured,
    get_current_node,
    set_current_node,
    start_capture,
    stop_capture,
)
from webapp.llm_settings import apply_llm_config
from webapp.execution import (
    build_order,
    fetch_last_close_price,
    get_account_overview,
    get_alpaca_credentials,
    get_alpaca_portfolio_context,
    get_alpaca_trading_client,
    parse_trader_decision,
    submit_alpaca_order,
)

logger = logging.getLogger(__name__)


class AnalysisRunner:
    """Manages background agent analysis tasks, locks per ticker, and streams logs."""

    def __init__(self, max_workers: int = 2):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="analysis-worker")
        self._lock = threading.Lock()
        self._in_flight_tickers: set[str] = set()
        self._active_runs: dict[str, dict[str, Any]] = {}

    def is_ticker_running(self, ticker: str) -> bool:
        clean = ticker.strip().upper()
        with self._lock:
            return clean in self._in_flight_tickers

    def get_in_flight_tickers(self) -> list[str]:
        with self._lock:
            return sorted(list(self._in_flight_tickers))

    def get_run_memory_logs(self, run_id: str) -> str:
        with self._lock:
            info = self._active_runs.get(run_id)
            if info:
                return info.get("log_buffer", "")
        return ""

    def get_run_live_calls(self, run_id: str) -> list[dict[str, Any]]:
        return get_captured(run_id=run_id)

    def get_current_node(self, run_id: str) -> str | None:
        return get_current_node(run_id=run_id)

    def get_in_flight_details(self) -> list[dict[str, Any]]:
        with self._lock:
            active_items = list(self._active_runs.items())
        details = []
        for rid, info in active_items:
            live_calls = self.get_run_live_calls(rid)
            cur_node = self.get_current_node(rid) or "Initializing"
            ticker_sym = info.get("ticker")
            formatted_calls = []
            if live_calls:
                for c in live_calls[-10:]:
                    c_dict = dict(c)
                    if not c_dict.get("ticker"):
                        c_dict["ticker"] = ticker_sym
                    formatted_calls.append(c_dict)
            details.append({
                "run_id": rid,
                "ticker": ticker_sym,
                "trade_date": info.get("trade_date"),
                "status": "running",
                "current_node": cur_node,
                "call_count": len(live_calls),
                "latest_calls": formatted_calls,
                "started_at": info.get("started_at"),
            })
        return details

    def start_analysis(
        self,
        ticker: str,
        trade_date: str | None = None,
        trigger: str = "manual",
        from_log_path: str | None = None,
    ) -> tuple[str, bool, str]:
        """Trigger an analysis run for a single ticker in the background.

        Returns (run_id, success, message).
        """
        clean_ticker = ticker.strip().upper()
        if not clean_ticker:
            return "", False, "Ticker cannot be empty"

        if trade_date is None:
            trade_date = datetime.now().strftime("%Y-%m-%d")

        with self._lock:
            if clean_ticker in self._in_flight_tickers:
                return (
                    "",
                    False,
                    f"Analysis for {clean_ticker} is already in flight. Please wait for it to complete.",
                )
            self._in_flight_tickers.add(clean_ticker)

        run_id = str(uuid.uuid4())
        create_run(run_id, clean_ticker, trade_date, trigger=trigger)

        with self._lock:
            self._active_runs[run_id] = {
                "ticker": clean_ticker,
                "trade_date": trade_date,
                "trigger": trigger,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "log_buffer": "",
            }

        self._executor.submit(
            self._execute_job,
            run_id,
            clean_ticker,
            trade_date,
            trigger,
            from_log_path,
        )
        return run_id, True, f"Started analysis for {clean_ticker}"

    def start_watchlist_analysis(self, trigger: str = "manual") -> list[dict[str, Any]]:
        """Start analysis for all enabled tickers in the watchlist."""
        active = get_active_watchlist()
        results = []
        for sym in active:
            run_id, success, message = self.start_analysis(sym, trigger=trigger)
            results.append({
                "ticker": sym,
                "run_id": run_id if success else None,
                "started": success,
                "message": message,
            })
        return results

    def _log(self, run_id: str, message: str) -> None:
        """Append log line with UTC timestamp to in-memory buffer and database."""
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
        line = f"[{now_str}] {message}\n"
        with self._lock:
            if run_id in self._active_runs:
                self._active_runs[run_id]["log_buffer"] += line
        try:
            append_run_log(run_id, line)
        except Exception as e:
            logger.error("Failed to append log to DB: %s", e)

    def _execute_job(
        self,
        run_id: str,
        ticker: str,
        trade_date: str,
        trigger: str,
        from_log_path: str | None = None,
    ) -> None:
        """Worker thread body executing the full analysis and auto-trade lifecycle."""
        start_capture(run_id=run_id)
        set_current_node("Initializing")
        try:
            self._log(run_id, f"🚀 Initializing run {run_id[:8]} for {ticker} (Date: {trade_date}, Trigger: {trigger})")

            # 1. Alpaca account & holdings context
            self._log(run_id, "🔍 Connecting to Alpaca to fetch current account & position context...")
            acct_info = None
            client = None
            held_qty = 0.0
            held_avg_price = None
            account_equity = 100000.0

            try:
                client = get_alpaca_trading_client()
                acct_info = get_account_overview(client)
                account_equity = acct_info["equity"]
                paper_label = "Paper" if acct_info["paper"] else "LIVE"
                self._log(
                    run_id,
                    f"💰 Alpaca {paper_label} account: ${account_equity:,.2f} equity, "
                    f"${acct_info['cash']:,.2f} cash",
                )

                # Check current position for this ticker
                matching = next((p for p in acct_info["positions"] if p["symbol"] == ticker), None)
                if matching:
                    held_qty = float(matching["qty"])
                    held_avg_price = matching["avg_entry_price"]
                    self._log(
                        run_id,
                        f"📊 Existing position detected: {held_qty} shares @ ${held_avg_price:,.2f} "
                        f"(Market value: ${matching.get('market_value', 0):,.2f})",
                    )
                else:
                    self._log(run_id, f"📊 No existing position held in {ticker} (Flat).")
            except Exception as alpaca_err:
                self._log(run_id, f"⚠️ Warning: Alpaca context lookup failed: {alpaca_err}")

            # 2. Build PortfolioContext for TradingAgents
            portfolio_ctx = get_alpaca_portfolio_context(client) if client else None

            # 3. Run TradingAgents pipeline or load from log
            final_state = None
            signal = None

            if from_log_path:
                self._log(run_id, f"📂 Loading decision from saved log: {from_log_path}")
                with open(from_log_path, encoding="utf-8") as f:
                    final_state = json.load(f)
                signal = "(loaded from log)"
            else:
                self._log(run_id, f"🧠 Spawning TradingAgents multi-agent pipeline for {ticker}...")
                self._log(run_id, "⏳ Analysts collecting market data, technicals, news, and fundamentals...")

                from tradingagents.default_config import DEFAULT_CONFIG
                from tradingagents.graph.trading_graph import TradingAgentsGraph

                config = DEFAULT_CONFIG.copy()
                # Apply the LLM provider configured in the dashboard (takes precedence
                # over .env / TRADINGAGENTS_* for the provider, key, and models).
                apply_llm_config(config)
                self._log(run_id, f"🧠 LLM provider: {config.get('llm_provider')} | deep: {config.get('deep_think_llm')} | quick: {config.get('quick_think_llm')}")
                ta = TradingAgentsGraph(config=config)

                self._log(run_id, "🤖 Executing agent consensus debate and risk management...")
                final_state, signal = ta.propagate(ticker, trade_date, portfolio=portfolio_ctx)
                self._log(run_id, f"✨ Graph execution completed with consensus signal: {signal}")

            # 4. Parse decision
            self._log(run_id, "📋 Parsing structured recommendation...")
            decision = parse_trader_decision(final_state)
            action = decision.get("action")
            rating = decision.get("rating")
            entry_price = decision.get("entry_price")
            stop_loss = decision.get("stop_loss")
            price_target = decision.get("price_target")
            sizing = decision.get("position_sizing")
            reasoning = decision.get("reasoning")

            self._log(
                run_id,
                f"🎯 Decision summary: Action={action or 'N/A'}, Rating={rating or 'N/A'}, "
                f"Entry=${entry_price if entry_price else 0:.2f}, Stop=${stop_loss if stop_loss else 0:.2f}, "
                f"Target=${price_target if price_target else 0:.2f}, Sizing={sizing or 'default'}",
            )

            # 5. Persist recommendation in SQLite
            rec_id = create_recommendation(
                run_id=run_id,
                ticker=ticker,
                trade_date=trade_date,
                action=action,
                rating=rating,
                entry_price=entry_price,
                stop_loss=stop_loss,
                price_target=price_target,
                position_sizing=sizing,
                reasoning=reasoning,
                current_position_qty=held_qty,
                current_position_avg_price=held_avg_price,
                final_trade_decision=final_state.get("final_trade_decision"),
                trader_investment_plan=final_state.get("trader_investment_plan"),
            )
            self._log(run_id, f"💾 Stored recommendation #{rec_id} in database.")

            # 6. Check auto-trade status and place or skip order
            auto_trade = is_auto_trade_enabled()
            self._log(run_id, f"⚙️ Auto-trade toggle is currently: {'ENABLED (LIVE/PAPER SUBMISSION)' if auto_trade else 'DISABLED (ADVISORY ONLY)'}")

            if not auto_trade:
                # Advisory Mode active: Run remains in Advisory state, no order records created/generated
                self._log(run_id, "ℹ️ Advisory Mode active: Auto-trade is OFF. Run remains in Advisory state (no order records generated).")
                completed_at = datetime.now(timezone.utc).isoformat()
                update_run_status(run_id, "advisory", completed_at=completed_at)
                self._log(run_id, "🎉 Advisory analysis completed successfully.")
            else:
                # Fetch reference price if needed
                current_market_price = fetch_last_close_price(ticker) if client else None
                order_spec = build_order(ticker, decision, account_equity, current_market_price)

                if order_spec is None:
                    skip_msg = f"No order generated: Action '{action}' / Rating '{rating}' evaluated to HOLD/REVIEW."
                    self._log(run_id, f"⏸️ {skip_msg}")
                    create_order_record(
                        run_id=run_id,
                        recommendation_id=rec_id,
                        ticker=ticker,
                        side=action.lower() if action in ("BUY", "SELL") else "hold",
                        order_type="none",
                        qty=None,
                        notional=None,
                        limit_price=None,
                        stop_price=None,
                        take_profit_price=None,
                        status="skipped",
                        skip_reason=skip_msg,
                    )
                elif order_spec["side"] == "sell" and held_qty <= 0:
                    skip_msg = f"Skipped SELL order: Cannot sell {ticker} because you do not hold a position in {ticker} (0 shares held)."
                    self._log(run_id, f"⏸️ {skip_msg}")
                    create_order_record(
                        run_id=run_id,
                        recommendation_id=rec_id,
                        ticker=ticker,
                        side="sell",
                        order_type="none",
                        qty=None,
                        notional=None,
                        limit_price=None,
                        stop_price=None,
                        take_profit_price=None,
                        status="skipped",
                        skip_reason=skip_msg,
                    )
                else:
                    if order_spec["side"] == "sell" and held_qty > 0 and order_spec.get("qty"):
                        order_spec["qty"] = min(int(order_spec["qty"]), int(held_qty))
                    self._log(
                        run_id,
                        f"📦 Order specification: {order_spec['side'].upper()} {order_spec['symbol']} "
                        f"Type={order_spec['otype']}, Qty={order_spec.get('qty')}, "
                        f"Limit=${order_spec.get('limit_price') or 0:.2f}, Stop=${order_spec.get('stop_price') or 0:.2f}, "
                        f"Target=${order_spec.get('take_profit_price') or 0:.2f}",
                    )

                    if client is None:
                        client = get_alpaca_trading_client()
                    self._log(run_id, "🚀 Submitting order to Alpaca...")
                    try:
                        submitted = submit_alpaca_order(order_spec, client=client)
                        self._log(run_id, f"✅ Order successfully submitted! Alpaca Order ID: {submitted['id']}")
                        create_order_record(
                            run_id=run_id,
                            recommendation_id=rec_id,
                            ticker=ticker,
                            side=order_spec["side"],
                            order_type=order_spec["otype"],
                            qty=order_spec.get("qty"),
                            notional=order_spec.get("notional"),
                            limit_price=order_spec.get("limit_price"),
                            stop_price=order_spec.get("stop_price"),
                            take_profit_price=order_spec.get("take_profit_price"),
                            status="submitted",
                            alpaca_order_id=submitted.get("id"),
                            client_order_id=submitted.get("client_order_id"),
                            raw_response=submitted,
                        )
                    except Exception as order_err:
                        err_str = f"Alpaca order submission failed: {order_err}"
                        self._log(run_id, f"❌ {err_str}")
                        create_order_record(
                            run_id=run_id,
                            recommendation_id=rec_id,
                            ticker=ticker,
                            side=order_spec["side"],
                            order_type=order_spec["otype"],
                            qty=order_spec.get("qty"),
                            notional=order_spec.get("notional"),
                            limit_price=order_spec.get("limit_price"),
                            stop_price=order_spec.get("stop_price"),
                            take_profit_price=order_spec.get("take_profit_price"),
                            status="failed",
                            error_message=err_str,
                        )

                completed_at = datetime.now(timezone.utc).isoformat()
                update_run_status(run_id, "completed", completed_at=completed_at)
                self._log(run_id, "🎉 Analysis completed successfully.")

        except Exception as exc:
            tb = traceback.format_exc()
            self._log(run_id, f"💥 Execution error: {exc}\n{tb}")
            completed_at = datetime.now(timezone.utc).isoformat()
            update_run_status(run_id, "failed", completed_at=completed_at, error=str(exc))
        finally:
            try:
                captured = get_captured(run_id=run_id)
                stop_capture()
                if captured:
                    insert_llm_calls(run_id, captured)
                    self._log(run_id, f"💾 Persisted {len(captured)} LLM & tool calls to database.")
            except Exception as capture_err:
                logger.error("Failed to persist captured LLM calls for run %s: %s", run_id, capture_err)
            with self._lock:
                self._in_flight_tickers.discard(ticker)
                self._active_runs.pop(run_id, None)


# Global runner singleton
runner = AnalysisRunner()
