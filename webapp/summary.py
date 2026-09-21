"""Deterministic decision summarizer that turns an agent run into plain-English steps."""
from __future__ import annotations

import re
from typing import Any, Callable


def _extract_first_sentences(text: Any, max_sentences: int = 2, max_chars: int = 200) -> str:
    """Extract clean, non-technical plain sentences from markdown or structured string."""
    if not text:
        return ""
    if not isinstance(text, str):
        if isinstance(text, dict):
            # Try to grab common summary/message fields
            for k in (
                "content",
                "reasoning",
                "executive_summary",
                "investment_thesis",
                "summary",
                "plan",
                "investment_plan",
                "narrative",
                "rationale",
            ):
                val = text.get(k)
                if val:
                    res = _extract_first_sentences(val, max_sentences, max_chars)
                    if res:
                        return res
            return ""
        text = str(text)

    # Avoid echoing fallback note as a finding
    if "structured output returned nothing" in text:
        return ""

    # Strip markdown horizontal rules (---, ___, ***)
    cleaned = re.sub(r"^\s*[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Strip entire heading lines (# Title) if remaining body text exists; otherwise strip hashes
    heading_stripped = re.sub(r"^#+\s*.*$", "", cleaned, flags=re.MULTILINE).strip()
    if heading_stripped:
        cleaned = heading_stripped
    else:
        cleaned = re.sub(r"#+\s*", "", cleaned)

    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"\*([^*]+)\*", r"\1", cleaned)
    cleaned = re.sub(r"^[-*]\s+", "", cleaned, flags=re.MULTILINE)

    # Filter out metadata lines like 'Analysis date: ... | Instrument: ...'
    lines = []
    for line in cleaned.splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        if ("analysis date:" in line_s.lower() or "as of " in line_s.lower()) and (
            "|" in line_s or line_s.endswith("close")
        ):
            continue
        lines.append(line_s)
    cleaned = " ".join(lines)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    selected = []
    total_len = 0
    for s in sentences:
        s_clean = s.strip()
        if not s_clean or len(s_clean) < 8:
            continue
        # Skip section titles like "Executive Summary:"
        if s_clean.endswith(":") and len(s_clean.split()) <= 4:
            continue
        selected.append(s_clean)
        total_len += len(s_clean)
        if len(selected) >= max_sentences or total_len >= max_chars:
            break

    result = " ".join(selected)
    if len(result) > max_chars:
        result = result[:max_chars].rstrip() + "..."
    return result


def _get_call_content_length(call: dict[str, Any]) -> int:
    """Return effective length of meaningful textual response content in a call."""
    if not call or not call.get("ok"):
        return 0
    resp = call.get("response")
    if resp is None:
        return 0
    if isinstance(resp, str):
        cleaned = resp.strip()
        if cleaned == "(tool call requested by model)" or "structured output returned nothing" in cleaned:
            return 0
        return len(cleaned)
    if isinstance(resp, dict):
        if "content" in resp:
            c = resp.get("content")
            return len(c.strip()) if isinstance(c, str) else len(str(c).strip()) if c else 0
        for k in ("narrative", "reasoning", "executive_summary", "rationale", "investment_plan", "summary"):
            val = resp.get(k)
            if val and isinstance(val, str) and val.strip():
                return len(val.strip())
        if resp.get("overall_band") or resp.get("band"):
            return 50
        return len(str(resp))
    return len(str(resp))


def _select_representative_call(
    calls: list[dict[str, Any]],
    node_predicate: Callable[[dict[str, Any]], bool],
    prefer_kind: str | None = "llm",
) -> dict[str, Any] | None:
    """Select representative call for a node preferring non-empty responses.

    Prefers the call with the longest meaningful response (or the last non-empty call).
    Falls back to the last available call if all responses are empty.
    """
    matching = [c for c in calls if node_predicate(c) and c.get("ok")]
    if not matching:
        matching = [c for c in calls if node_predicate(c)]
    if not matching:
        return None

    if prefer_kind:
        kind_matching = [c for c in matching if c.get("kind") == prefer_kind]
        if kind_matching:
            matching = kind_matching

    non_empty = [c for c in matching if _get_call_content_length(c) > 0]
    if non_empty:
        return max(non_empty, key=lambda c: (_get_call_content_length(c), c.get("seq", 0)))
    return matching[-1]


def _to_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float, returning default on None, invalid string, or error."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _extract_analyst_key_find(calls: list[dict[str, Any]]) -> str:
    """Extract key finding from market/sentiment/fundamentals analysts."""
    snippets = []

    # Check for sentiment report
    sentiment_call = _select_representative_call(calls, lambda c: "Sentiment" in (c.get("node") or ""))
    if sentiment_call:
        resp = sentiment_call.get("response")
        if isinstance(resp, dict):
            band = resp.get("overall_band") or resp.get("band") or resp.get("sentiment_band")
            score = resp.get("overall_score") or resp.get("score") or resp.get("sentiment_score")
            if band:
                score_num = _to_float(score, default=-1.0)
                if 0 <= score_num <= 10:
                    score_str = f" ({score}/10)"
                elif score is not None:
                    score_str = f" ({score}/100)"
                else:
                    score_str = ""
                snippets.append(f"Sentiment: {band}{score_str}")
            elif resp.get("content"):
                first = _extract_first_sentences(resp.get("content"), max_sentences=1, max_chars=90)
                if first:
                    snippets.append(first)
        elif isinstance(resp, str):
            first = _extract_first_sentences(resp, max_sentences=1, max_chars=90)
            if first:
                snippets.append(first)

    # Check for market analyst report
    market_call = _select_representative_call(calls, lambda c: "Market" in (c.get("node") or ""))
    if market_call:
        first = _extract_first_sentences(market_call.get("response"), max_sentences=1, max_chars=110)
        if first and "Select indicators" not in first and "tool" not in first.lower():
            snippets.append(first)

    # Check fundamentals analyst
    if len(snippets) < 2:
        fund_call = _select_representative_call(calls, lambda c: "Fundamentals" in (c.get("node") or ""))
        if fund_call:
            first = _extract_first_sentences(fund_call.get("response"), max_sentences=1, max_chars=100)
            if first and "tool" not in first.lower():
                snippets.append(first)

    if snippets:
        return " | ".join(snippets)
    return "Consolidated price, technical indicators, news sentiment, and financial fundamentals."


def _map_node_to_step(node: str | None) -> int:
    """Map LangGraph node or agent name to pipeline step number (1 to 5)."""
    if not node:
        return 1
    node_lower = node.lower()
    if any(k in node_lower for k in ("market", "sentiment", "social", "news", "fundamental")):
        return 1
    if any(k in node_lower for k in ("bull", "bear", "research manager")):
        return 2
    if "trader" in node_lower:
        return 3
    if any(k in node_lower for k in ("aggressive", "conservative", "neutral", "portfolio")):
        return 4
    if any(k in node_lower for k in ("execution", "advisory", "order", "alpaca")):
        return 5
    return 1


def summarize_run(
    run: dict[str, Any],
    llm_calls: list[dict[str, Any]] | None = None,
    recommendation: dict[str, Any] | None = None,
    order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Deterministically produce a plain-English, step-by-step summary of a run."""
    llm_calls = llm_calls or []
    rec = recommendation or run.get("recommendation") or {}
    ord_info = order or run.get("order") or {}

    ticker = (run.get("ticker") or rec.get("ticker") or "STOCK").upper()
    action = (rec.get("action") or "").upper()
    rating = rec.get("rating") or "Neutral"
    entry = rec.get("entry_price")
    stop = rec.get("stop_loss")
    target = rec.get("price_target")
    entry_f = _to_float(entry, 0.0) if entry is not None else None
    stop_f = _to_float(stop, 0.0)
    target_f = _to_float(target, 0.0)
    sizing = rec.get("position_sizing") or "5% of portfolio"
    status = run.get("status", "completed")

    # Detect failed calls to identify failing step if run failed
    failed_calls = [c for c in llm_calls if c and c.get("ok") is False]
    last_failed_call = None
    failed_node = None
    failed_err_msg = None
    if failed_calls:
        last_failed_call = max(
            failed_calls,
            key=lambda c: (c.get("seq") or 0, str(c.get("ts") or "")),
        )
        failed_node = (
            last_failed_call.get("node")
            or last_failed_call.get("agent")
            or "Unknown Step"
        )
        raw_err = (
            last_failed_call.get("error")
            or last_failed_call.get("response")
            or run.get("error")
            or "Unknown error"
        )
        err_msg = str(raw_err).strip()
        if "\n" in err_msg:
            lines = [line.strip() for line in err_msg.splitlines() if line.strip()]
            if lines:
                err_msg = lines[-1]
        failed_err_msg = err_msg

    # 1. Overall banner string
    if ord_info.get("status") == "submitted":
        qty = ord_info.get("qty", "?")
        order_type = (ord_info.get("order_type") or "bracket").lower()
        limit_p = _to_float(ord_info.get("limit_price"), entry_f if entry_f is not None else 0.0)
        stop_p = _to_float(ord_info.get("stop_price"), stop_f)
        tp_p = _to_float(ord_info.get("take_profit_price"), target_f)
        overall = f"{action or 'ORDER'} {ticker} — {qty} shares @ ~${limit_p:.2f} ({order_type}: stop ${stop_p:.2f}, target ${tp_p:.2f})"
    elif action in ("BUY", "SELL"):
        if entry_f is not None:
            overall = f"{action} {ticker} — Entry ~${entry_f:.2f} (bracket: stop ${stop_f:.2f}, target ${target_f:.2f})"
        else:
            overall = f"{action} {ticker} — {rating}"
    elif action == "HOLD":
        overall = f"HOLD {ticker} — Risk/reward balanced; maintaining current position"
    elif status == "failed":
        if failed_node and failed_err_msg:
            overall = f"FAILED at [{failed_node}]: {failed_err_msg}"
        else:
            overall = f"FAILED: Analysis encountered an error ({run.get('error') or 'check logs'})"
    elif status == "running":
        overall = f"ANALYSIS IN PROGRESS for {ticker}"
    else:
        overall = f"{action or 'ANALYSIS'} {ticker} — {rating}"

    # 2. Step 1: Market & Fundamentals
    s1_calls = [
        c for c in llm_calls
        if any(a in (c.get("node") or "") for a in ("Market", "Sentiment", "News", "Fundamentals"))
    ]
    s1_llm_count = len([c for c in s1_calls if c.get("kind") == "llm"])
    s1_tools = sorted(list({
        c.get("tool_name") for c in s1_calls
        if c.get("tool_name") and c.get("kind") == "tool"
    }))
    # If no explicit kind='tool' found, check any call tool_name or tool_calls
    if not s1_tools:
        s1_tools = sorted(list({
            c.get("tool_name") for c in s1_calls
            if c.get("tool_name") and not str(c.get("tool_name")).endswith("Report")
        }))

    s1_key_find = _extract_analyst_key_find(s1_calls)

    # 3. Step 2: Debate Bull vs Bear
    s2_calls = [
        c for c in llm_calls
        if any(r in (c.get("node") or "") for r in ("Bull", "Bear", "Research Manager"))
    ]
    s2_llm_count = len([c for c in s2_calls if c.get("kind") == "llm"])

    # Extract Research Manager synthesis
    rm_call = _select_representative_call(s2_calls, lambda c: "Research Manager" in (c.get("node") or ""))
    rm_quote = ""
    if rm_call:
        rm_quote = _extract_first_sentences(rm_call.get("response"), max_sentences=2, max_chars=180)

    # Fallback to recommendation text or thesis
    if not rm_quote and rec.get("trader_investment_plan"):
        rm_quote = _extract_first_sentences(rec.get("trader_investment_plan"), max_sentences=2, max_chars=180)

    if not rm_quote:
        rm_quote = "Debated growth catalysts against valuation/macro risks; synthesized consensus investment plan."

    # 4. Step 3: Trader Position Sizing
    s3_calls = [c for c in llm_calls if "Trader" in (c.get("node") or "")]
    s3_llm_count = len([c for c in s3_calls if c.get("kind") == "llm"])

    if action in ("BUY", "SELL"):
        s3_what = f"Chose {action} with {sizing}."
        if entry_f is not None:
            s3_key_find = f"Entry ${entry_f:.2f}, stop ${stop_f:.2f}, target ${target_f:.2f}."
        else:
            s3_key_find = f"Action: {action}, sizing: {sizing}."
    else:
        s3_what = "Evaluated market structure and recommended HOLD."
        s3_key_find = "No new position initiated; maintaining defensive posture."

    if rec.get("reasoning"):
        clean_reason = _extract_first_sentences(rec.get("reasoning"), max_sentences=1, max_chars=120)
        if clean_reason:
            s3_key_find += f" Rationale: {clean_reason}"
    else:
        trader_call = _select_representative_call(s3_calls, lambda c: "Trader" in (c.get("node") or ""))
        if trader_call:
            clean_reason = _extract_first_sentences(trader_call.get("response"), max_sentences=1, max_chars=120)
            if clean_reason:
                s3_key_find += f" Rationale: {clean_reason}"

    # 5. Step 4: Risk Team Stress Test
    s4_calls = [
        c for c in llm_calls
        if any(p in (c.get("node") or "") for p in ("Aggressive", "Conservative", "Neutral", "Portfolio Manager"))
    ]
    s4_llm_count = len([c for c in s4_calls if c.get("kind") == "llm"])

    pm_quote = ""
    pm_call = _select_representative_call(s4_calls, lambda c: "Portfolio Manager" in (c.get("node") or ""))
    if pm_call:
        pm_quote = _extract_first_sentences(pm_call.get("response"), max_sentences=2, max_chars=180)

    if not pm_quote and rec.get("final_trade_decision"):
        pm_quote = _extract_first_sentences(rec.get("final_trade_decision"), max_sentences=2, max_chars=180)

    if not pm_quote:
        pm_quote = f"Portfolio Manager finalized verdict: {rating} / {action or 'HOLD'}."

    s4_key_find = f"PM decision: {rating} / {action or 'HOLD'} — {pm_quote}"

    # 6. Step 5: Execution / Advisory
    ord_status = (ord_info.get("status") or "").lower() if ord_info else ""
    run_status = (status or "").lower()
    if ord_status == "submitted":
        s5_title = "Execution"
        s5_what = "Auto-trade was ENABLED; order submitted to Alpaca."
        s5_key_find = (
            f"Submitted {ord_info.get('qty', '?')}-share {ord_info.get('order_type', 'bracket').upper()} "
            f"bracket to Alpaca (paper). Alpaca Order ID: {ord_info.get('alpaca_order_id', 'confirmed')}."
        )
    elif ord_status == "skipped":
        s5_title = "Execution"
        s5_what = "Auto-trade was DISABLED (Advisory Mode) or order condition skipped."
        s5_key_find = ord_info.get("skip_reason") or "Order skipped: Advisory mode only (no live order placed)."
    elif ord_status == "failed":
        s5_title = "Execution"
        s5_what = "Order submission attempted but encountered an error."
        s5_key_find = ord_info.get("error_message") or "Order submission failed."
    elif run_status == "advisory" or not ord_info:
        s5_title = "Advisory"
        s5_what = "Advisory analysis completed."
        s5_key_find = "No order submitted (run in advisory mode)."
    else:
        s5_title = "Execution"
        s5_what = "Advisory analysis completed."
        s5_key_find = "No order submitted (run in advisory mode)."

    steps = [
        {
            "n": 1,
            "title": "Gathered market & fundamentals",
            "who": "Market, Sentiment, News & Fundamentals Analysts",
            "what": "Pulled price/technical, news, and balance-sheet data.",
            "key_find": s1_key_find,
            "llm_count": s1_llm_count,
            "tools": s1_tools,
        },
        {
            "n": 2,
            "title": "Debate: Bull vs Bear",
            "who": "Bull Researcher vs Bear Researcher → Research Manager",
            "what": "Bull argued catalysts; Bear highlighted valuation; manager decided direction.",
            "key_find": rm_quote,
            "llm_count": s2_llm_count,
        },
        {
            "n": 3,
            "title": "Trader sized the position",
            "who": "Trader",
            "what": s3_what,
            "key_find": s3_key_find,
            "llm_count": s3_llm_count,
        },
        {
            "n": 4,
            "title": "Risk team stress-tested it",
            "who": "Aggressive / Conservative / Neutral Analysts → Portfolio Manager",
            "what": "Aggressive evaluated upside; Conservative checked downside; PM approved.",
            "key_find": s4_key_find,
            "llm_count": s4_llm_count,
        },
        {
            "n": 5,
            "title": s5_title,
            "who": "Dashboard",
            "what": s5_what,
            "key_find": s5_key_find,
        },
    ]

    if status == "failed":
        if last_failed_call and failed_node:
            failed_step_num = _map_node_to_step(failed_node)
            for step in steps:
                step_n = step.get("n", 0)
                if step_n == failed_step_num:
                    step["failed"] = True
                    step["error"] = failed_err_msg
                    step["key_find"] = f"\u274c No output \u2014 {failed_node} failed: {failed_err_msg}"
                elif step_n > failed_step_num:
                    step["skipped"] = True
                    step["key_find"] = "Skipped due to upstream failure"
        else:
            run_err = str(run.get("error") or "check logs").strip()
            failed_step_num = _map_node_to_step(run_err) if run.get("error") else 1
            for step in steps:
                step_n = step.get("n", 0)
                if step_n == failed_step_num:
                    step["failed"] = True
                    step["error"] = run_err
                    step["key_find"] = f"\u274c No output \u2014 step failed: {run_err}"
                elif step_n > failed_step_num:
                    step["skipped"] = True
                    step["key_find"] = "Skipped due to upstream failure"

    return {
        "overall": overall,
        "confidence": rating,
        "steps": steps,
    }

