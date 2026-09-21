"""Unit tests for the 3 TradingAgents dashboard fixes:
1. ToolNodeSpy captures actual tool output in LLM Calls
2. 503 UNAVAILABLE retry logic in LLMSpy and BoundLLMSpy
3. Summary failure identification and step skipping
"""
import unittest.mock as mock
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tradingagents.llm_clients.llm_trace import (
    BoundLLMSpy,
    LLMSpy,
    ToolNodeSpy,
    is_capturing,
    record,
    start_capture,
    stop_capture,
    MAX_RETRIES,
    RETRY_DELAY_SECONDS,
)
from webapp.summary import summarize_run, _map_node_to_step


# ============================================================================
# Issue 1: ToolNodeSpy Output Capture Tests
# ============================================================================


def test_tool_node_spy_captures_output():
    start_capture("test-run-tool")
    try:
        mock_underlying = mock.MagicMock()
        mock_underlying.invoke.return_value = {
            "messages": [
                ToolMessage(
                    content='{"ticker": "AAPL", "price": 175.50}',
                    name="get_stock_data",
                    tool_call_id="call_abc_1",
                )
            ]
        }

        spy = ToolNodeSpy(mock_underlying, node_name="Market Analyst")

        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "get_stock_data",
                    "args": {"ticker": "AAPL"},
                    "id": "call_abc_1",
                }
            ],
        )
        state = {"messages": [HumanMessage(content="Analyze AAPL"), ai_msg]}

        result = spy.invoke(state)
        assert result == mock_underlying.invoke.return_value

        captured = stop_capture()
        tool_records = [c for c in captured if c.get("kind") == "tool"]
        assert len(tool_records) == 1
        rec = tool_records[0]
        assert rec["tool_name"] == "get_stock_data"
        assert rec["request"] == {"tool": "get_stock_data", "args": {"ticker": "AAPL"}}
        assert rec["response"] == '{"ticker": "AAPL", "price": 175.50}'
        assert rec["ok"] is True
        assert rec["node"] == "Market Analyst"
    finally:
        if is_capturing():
            stop_capture()


def test_tool_node_spy_multiple_tools_matched_by_id():
    start_capture("test-run-multi-tool")
    try:
        mock_underlying = mock.MagicMock()
        mock_underlying.invoke.return_value = {
            "messages": [
                ToolMessage(
                    content="Price history data",
                    name="get_stock_data",
                    tool_call_id="call_1",
                ),
                ToolMessage(
                    content="RSI=65, MACD=bullish",
                    name="get_indicators",
                    tool_call_id="call_2",
                ),
            ]
        }

        spy = ToolNodeSpy(mock_underlying)

        ai_msg = AIMessage(
            content="",
            tool_calls=[
                {"name": "get_stock_data", "args": {"ticker": "MSFT"}, "id": "call_1"},
                {"name": "get_indicators", "args": {"ticker": "MSFT", "indicator": "RSI"}, "id": "call_2"},
            ],
        )
        state = {"messages": [ai_msg]}

        spy.invoke(state)

        captured = stop_capture()
        tool_records = [c for c in captured if c.get("kind") == "tool"]
        assert len(tool_records) == 2

        assert tool_records[0]["tool_name"] == "get_stock_data"
        assert tool_records[0]["response"] == "Price history data"
        assert tool_records[0]["ok"] is True

        assert tool_records[1]["tool_name"] == "get_indicators"
        assert tool_records[1]["response"] == "RSI=65, MACD=bullish"
        assert tool_records[1]["ok"] is True
    finally:
        if is_capturing():
            stop_capture()


def test_tool_node_spy_passthrough_when_not_capturing():
    assert not is_capturing()
    mock_underlying = mock.MagicMock()
    mock_underlying.invoke.return_value = {"messages": ["done"]}
    spy = ToolNodeSpy(mock_underlying)

    state = {"messages": []}
    res = spy.invoke(state)
    assert res == {"messages": ["done"]}
    mock_underlying.invoke.assert_called_once_with(state)


def test_tool_node_spy_handles_exception():
    start_capture("test-run-tool-exc")
    try:
        mock_underlying = mock.MagicMock()
        mock_underlying.invoke.side_effect = RuntimeError("Data source timeout")

        spy = ToolNodeSpy(mock_underlying, node_name="Market Analyst")
        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}, "id": "c1"}],
        )
        state = {"messages": [ai_msg]}

        with pytest.raises(RuntimeError, match="Data source timeout"):
            spy.invoke(state)

        captured = stop_capture()
        tool_records = [c for c in captured if c.get("kind") == "tool"]
        assert len(tool_records) == 1
        assert tool_records[0]["ok"] is False
        assert "Data source timeout" in tool_records[0]["response"]
        assert tool_records[0]["tool_name"] == "get_stock_data"
    finally:
        if is_capturing():
            stop_capture()


def test_tool_node_spy_handles_tool_error_message():
    start_capture("test-run-tool-msg-err")
    try:
        mock_underlying = mock.MagicMock()
        mock_underlying.invoke.return_value = {
            "messages": [
                ToolMessage(
                    content="Rate limit reached for API key",
                    name="get_stock_data",
                    tool_call_id="call_err",
                    status="error",
                )
            ]
        }

        spy = ToolNodeSpy(mock_underlying)
        ai_msg = AIMessage(
            content="",
            tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}, "id": "call_err"}],
        )
        spy.invoke({"messages": [ai_msg]})

        captured = stop_capture()
        tool_records = [c for c in captured if c.get("kind") == "tool"]
        assert len(tool_records) == 1
        assert tool_records[0]["ok"] is False
        assert tool_records[0]["response"] == "Rate limit reached for API key"
    finally:
        if is_capturing():
            stop_capture()


def test_llm_spy_no_longer_emits_duplicate_placeholder_tool_records():
    start_capture("test-run-no-dup")
    try:
        mock_llm = mock.MagicMock()
        mock_llm.invoke.return_value = AIMessage(
            content="",
            tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}, "id": "c1"}],
        )

        spy = LLMSpy(mock_llm)
        spy.invoke("Please get stock data for AAPL")

        captured = stop_capture()
        # Should ONLY have kind="llm" record, NO kind="tool" records with placeholder
        tool_records = [c for c in captured if c.get("kind") == "tool"]
        assert len(tool_records) == 0

        llm_records = [c for c in captured if c.get("kind") == "llm"]
        assert len(llm_records) == 1
        assert llm_records[0]["ok"] is True
    finally:
        if is_capturing():
            stop_capture()


def test_bound_llm_spy_no_longer_emits_duplicate_placeholder_tool_records():
    start_capture("test-run-bound-no-dup")
    try:
        mock_bound = mock.MagicMock()
        mock_bound.invoke.return_value = AIMessage(
            content="",
            tool_calls=[{"name": "get_stock_data", "args": {"ticker": "AAPL"}, "id": "c1"}],
        )

        parent_spy = LLMSpy(mock.MagicMock())
        bound_spy = BoundLLMSpy(mock_bound, parent_spy)
        bound_spy.invoke("Please get stock data")

        captured = stop_capture()
        tool_records = [c for c in captured if c.get("kind") == "tool"]
        assert len(tool_records) == 0

        llm_records = [c for c in captured if c.get("kind") == "llm"]
        assert len(llm_records) == 1
    finally:
        if is_capturing():
            stop_capture()


# ============================================================================
# Issue 2: 503 Auto-Retry Logic Tests
# ============================================================================


def test_503_retry_succeeds_after_transient_failures():
    start_capture("test-run-503-success")
    try:
        mock_llm = mock.MagicMock()
        # Fail twice with 503 high demand, then succeed
        transient_exc = Exception("503 UNAVAILABLE: This model is currently experiencing high demand. Please try again later.")
        success_msg = AIMessage(content="Final successful analysis")
        mock_llm.invoke.side_effect = [transient_exc, transient_exc, success_msg]

        spy = LLMSpy(mock_llm, tier="quick", label="Market Analyst")

        with mock.patch("tradingagents.llm_clients.llm_trace.time.sleep") as mock_sleep:
            result = spy.invoke("Run market analysis")

        assert result == success_msg
        assert mock_sleep.call_count == 2
        mock_sleep.assert_has_calls([mock.call(60), mock.call(60)])

        captured = stop_capture()
        # Should have 2 retry log records (ok=False) + 1 final success record (ok=True)
        llm_records = [c for c in captured if c.get("kind") == "llm"]
        assert len(llm_records) == 3

        # First retry record
        assert llm_records[0]["ok"] is False
        assert "503 UNAVAILABLE" in llm_records[0]["error"]
        assert "will retry" in llm_records[0]["error"]

        # Second retry record
        assert llm_records[1]["ok"] is False
        assert "503 UNAVAILABLE" in llm_records[1]["error"]

        # Final success record
        assert llm_records[2]["ok"] is True
        assert llm_records[2]["error"] is None
        assert llm_records[2]["response"]["content"] == "Final successful analysis"
    finally:
        if is_capturing():
            stop_capture()


def test_503_retry_exhaustion_propagates_exception():
    start_capture("test-run-503-exhaust")
    try:
        mock_llm = mock.MagicMock()
        mock_llm.invoke.side_effect = Exception("503 UNAVAILABLE: overloaded")

        spy = LLMSpy(mock_llm)

        with mock.patch("tradingagents.llm_clients.llm_trace.time.sleep") as mock_sleep:
            with pytest.raises(Exception, match="503 UNAVAILABLE"):
                spy.invoke("Run analysis")

        # Must have attempted MAX_RETRIES sleeps (100 times)
        assert mock_sleep.call_count == MAX_RETRIES

        captured = stop_capture()
        llm_records = [c for c in captured if c.get("kind") == "llm"]
        # 100 retry attempt records + 1 final failure record from finally block = 101 records
        assert len(llm_records) == MAX_RETRIES + 1
        assert all(c["ok"] is False for c in llm_records)
        assert "503 UNAVAILABLE" in str(llm_records[-1]["error"])
    finally:
        if is_capturing():
            stop_capture()


@pytest.mark.parametrize(
    "error_string",
    [
        "503 Service Unavailable",
        "Resource_Exhausted: rate limit exceeded",
        "429 Too Many Requests",
        "Model is experiencing high demand",
        "System is overloaded",
    ],
)
def test_transient_error_detection_triggers_retry(error_string):
    mock_llm = mock.MagicMock()
    mock_llm.invoke.side_effect = [Exception(error_string), AIMessage(content="OK")]

    spy = LLMSpy(mock_llm)
    with mock.patch("tradingagents.llm_clients.llm_trace.time.sleep") as mock_sleep:
        res = spy.invoke("Test")

    assert res.content == "OK"
    assert mock_sleep.call_count == 1
    mock_sleep.assert_called_with(60)


def test_non_transient_error_fails_immediately_without_retry():
    mock_llm = mock.MagicMock()
    mock_llm.invoke.side_effect = ValueError("Invalid prompt parameters")

    spy = LLMSpy(mock_llm)
    with mock.patch("tradingagents.llm_clients.llm_trace.time.sleep") as mock_sleep:
        with pytest.raises(ValueError, match="Invalid prompt parameters"):
            spy.invoke("Test")

    assert mock_sleep.call_count == 0


def test_bound_llm_spy_retry_on_503():
    mock_bound = mock.MagicMock()
    mock_bound.invoke.side_effect = [
        Exception("503 UNAVAILABLE: high demand"),
        AIMessage(content="Bound success"),
    ]

    parent = LLMSpy(mock.MagicMock())
    spy = BoundLLMSpy(mock_bound, parent)

    with mock.patch("tradingagents.llm_clients.llm_trace.time.sleep") as mock_sleep:
        res = spy.invoke("Prompt")

    assert res.content == "Bound success"
    assert mock_sleep.call_count == 1


# ============================================================================
# Issue 3: Summary Failing Step Identification Tests
# ============================================================================


def test_map_node_to_step():
    assert _map_node_to_step("Market Analyst") == 1
    assert _map_node_to_step("tools_market") == 1
    assert _map_node_to_step("Sentiment Analyst") == 1
    assert _map_node_to_step("News Analyst") == 1
    assert _map_node_to_step("Fundamentals Analyst") == 1
    assert _map_node_to_step("Bull Researcher") == 2
    assert _map_node_to_step("Bear Researcher") == 2
    assert _map_node_to_step("Research Manager") == 2
    assert _map_node_to_step("Trader") == 3
    assert _map_node_to_step("Aggressive Analyst") == 4
    assert _map_node_to_step("Conservative Analyst") == 4
    assert _map_node_to_step("Neutral Analyst") == 4
    assert _map_node_to_step("Portfolio Manager") == 4
    assert _map_node_to_step("Execution") == 5
    assert _map_node_to_step("Advisory") == 5


def test_summary_identifies_market_analyst_failure():
    run = {
        "id": "failed-run-1",
        "ticker": "AAPL",
        "status": "failed",
        "error": "503 UNAVAILABLE",
    }
    llm_calls = [
        {
            "seq": 1,
            "node": "Market Analyst",
            "agent": "Market Analyst",
            "kind": "llm",
            "ok": False,
            "error": "503 UNAVAILABLE — high demand",
            "ts": "2026-09-21T10:00:00Z",
        }
    ]

    summary = summarize_run(run, llm_calls=llm_calls)

    # 1. Overall banner updated
    assert summary["overall"] == "FAILED at [Market Analyst]: 503 UNAVAILABLE — high demand"

    # 2. Step 1 marked failed
    steps = summary["steps"]
    assert len(steps) == 5

    s1 = steps[0]
    assert s1["n"] == 1
    assert s1.get("failed") is True
    assert s1.get("error") == "503 UNAVAILABLE — high demand"
    assert s1.get("skipped") is not True

    # 3. Steps 2-5 marked skipped
    for step in steps[1:]:
        assert step.get("skipped") is True
        assert step.get("key_find") == "Skipped due to upstream failure"
        assert step.get("failed") is not True


def test_summary_identifies_trader_failure():
    run = {
        "id": "failed-run-2",
        "ticker": "NVDA",
        "status": "failed",
        "error": "Trader timeout",
    }
    llm_calls = [
        {
            "seq": 1,
            "node": "Market Analyst",
            "agent": "Market Analyst",
            "kind": "llm",
            "ok": True,
            "response": "Market looks bullish.",
            "ts": "2026-09-21T10:00:00Z",
        },
        {
            "seq": 2,
            "node": "Research Manager",
            "agent": "Research Manager",
            "kind": "llm",
            "ok": True,
            "response": "Debate concluded: Bull thesis won.",
            "ts": "2026-09-21T10:01:00Z",
        },
        {
            "seq": 3,
            "node": "Trader",
            "agent": "Trader",
            "kind": "llm",
            "ok": False,
            "error": "429 Too Many Requests: Rate limit exceeded",
            "ts": "2026-09-21T10:02:00Z",
        },
    ]

    summary = summarize_run(run, llm_calls=llm_calls)

    assert summary["overall"] == "FAILED at [Trader]: 429 Too Many Requests: Rate limit exceeded"

    steps = summary["steps"]
    # Steps 1 and 2 completed normally
    assert steps[0].get("failed") is not True
    assert steps[0].get("skipped") is not True
    assert steps[1].get("failed") is not True
    assert steps[1].get("skipped") is not True

    # Step 3 failed
    assert steps[2]["n"] == 3
    assert steps[2].get("failed") is True
    assert steps[2].get("error") == "429 Too Many Requests: Rate limit exceeded"
    assert steps[2].get("skipped") is not True

    # Steps 4 and 5 skipped
    assert steps[3].get("skipped") is True
    assert steps[3].get("key_find") == "Skipped due to upstream failure"
    assert steps[4].get("skipped") is True
    assert steps[4].get("key_find") == "Skipped due to upstream failure"


def test_summary_selects_last_failed_call_when_multiple_present():
    run = {
        "id": "failed-run-3",
        "ticker": "TSLA",
        "status": "failed",
    }
    llm_calls = [
        {
            "seq": 1,
            "node": "Market Analyst",
            "kind": "llm",
            "ok": False,
            "error": "temporary warning",
            "ts": "2026-09-21T10:00:00Z",
        },
        {
            "seq": 2,
            "node": "Portfolio Manager",
            "kind": "llm",
            "ok": False,
            "error": "503 UNAVAILABLE — high demand",
            "ts": "2026-09-21T10:05:00Z",
        },
    ]

    summary = summarize_run(run, llm_calls=llm_calls)
    assert summary["overall"] == "FAILED at [Portfolio Manager]: 503 UNAVAILABLE — high demand"

    steps = summary["steps"]
    # Step 4 is Portfolio Manager
    assert steps[3].get("failed") is True
    assert steps[3].get("error") == "503 UNAVAILABLE — high demand"
    # Step 5 is skipped
    assert steps[4].get("skipped") is True
    assert steps[4].get("key_find") == "Skipped due to upstream failure"


def test_failed_step_does_not_show_faked_verdict():
    """Regression: when the Portfolio Manager (step 4) is the FAILED step, its
    key_find must NOT show a fabricated 'PM decision: Neutral / HOLD' verdict
    (that text was a default-value fallback that implied a real verdict existed
    even though the PM never produced one). It must instead surface the real
    error so the user can see the step genuinely produced no output.
    """
    run = {
        "id": "failed-run-pm-faked",
        "ticker": "QQQ",
        "status": "failed",
        "error": "503 UNAVAILABLE. high demand",
    }
    # Earlier steps produced real output; the Portfolio Manager call then 503'd.
    llm_calls = [
        {"seq": 1, "node": "Market Analyst", "kind": "llm", "ok": True,
         "response": "Uptrend intact with rising volume.", "ts": "2026-09-21T10:00:00Z"},
        {"seq": 2, "node": "Research Manager", "kind": "llm", "ok": True,
         "response": "Consensus leans neutral.", "ts": "2026-09-21T10:01:00Z"},
        {"seq": 3, "node": "Trader", "kind": "llm", "ok": True,
         "response": "Recommend hold pending clarification.", "ts": "2026-09-21T10:02:00Z"},
        {"seq": 4, "node": "Portfolio Manager", "kind": "llm", "ok": False,
         "error": "503 UNAVAILABLE. This model is currently experiencing high demand.",
         "ts": "2026-09-21T10:05:00Z"},
    ]

    # No real recommendation was stored for a failed run (rec empty / null).
    summary = summarize_run(run, llm_calls=llm_calls, recommendation=None, order=None)

    steps = summary["steps"]
    s4 = steps[3]
    assert s4["n"] == 4
    assert s4.get("failed") is True

    # The fabricated verdict must NOT be shown on the failing step.
    assert "PM decision: Neutral / HOLD" not in s4["key_find"]
    assert "Portfolio Manager finalized verdict" not in s4["key_find"]

    # It must surface the real error instead.
    assert "503 UNAVAILABLE" in s4["key_find"]
    assert s4["key_find"].startswith("\u274c No output")
    assert s4.get("error") == "503 UNAVAILABLE. This model is currently experiencing high demand."


# ============================================================================
# Progressive Summary in-progress tests (SPEC_progressive_summary.md)
# ============================================================================


def test_progressive_summary_running_no_calls():
    """A run with status='running' and NO calls:
    - Step 1 is 'active' (with an in-progress placeholder).
    - Steps 2-5 are 'pending' with empty key_find (no fabricated fallback findings).
    """
    run = {
        "id": "run-prog-no-calls",
        "ticker": "AAPL",
        "status": "running",
    }
    summary = summarize_run(run, llm_calls=[])
    assert summary["overall"] == "ANALYSIS IN PROGRESS for AAPL"

    steps = summary["steps"]
    assert len(steps) == 5

    # Step 1 is the initial active step
    assert steps[0]["n"] == 1
    assert steps[0]["state"] == "active"
    assert "In progress" in steps[0]["key_find"]

    # Steps 2-5 must be pending with empty key_find
    for s in steps[1:]:
        assert s["state"] == "pending"
        assert s["key_find"] == ""
        # Must never show fabricated fallback findings
        assert "Debated growth catalysts" not in s["key_find"]
        assert "PM decision" not in s["key_find"]
        assert "No order submitted" not in s["key_find"]
        assert "No new position initiated" not in s["key_find"]


def test_progressive_summary_running_only_step_1_calls():
    """A run with status='running' and only step-1 calls:
    - Step 1 is reached and 'active', displaying its real key finding.
    - Steps 2-5 are 'pending' with empty key_find and no fabricated findings.
    """
    run = {
        "id": "run-prog-step-1",
        "ticker": "MSFT",
        "status": "running",
    }
    llm_calls = [
        {
            "seq": 1,
            "node": "Market Analyst",
            "kind": "llm",
            "ok": True,
            "response": "Strong breakout pattern above 200-day moving average.",
            "ts": "2026-09-21T10:00:00Z",
        },
        {
            "seq": 2,
            "node": "Sentiment Analyst",
            "kind": "llm",
            "ok": True,
            "response": {"band": "Bullish", "score": 8.5},
            "ts": "2026-09-21T10:01:00Z",
        },
    ]

    summary = summarize_run(run, llm_calls=llm_calls)
    steps = summary["steps"]
    assert len(steps) == 5

    # Step 1 is active and has real findings
    assert steps[0]["n"] == 1
    assert steps[0]["state"] == "active"
    assert "Sentiment: Bullish (8.5/10)" in steps[0]["key_find"]
    assert "Strong breakout pattern" in steps[0]["key_find"]

    # Steps 2-5 must be pending with empty key_find
    for s in steps[1:]:
        assert s["state"] == "pending"
        assert s["key_find"] == ""
        assert "Debated growth catalysts" not in s["key_find"]
        assert "PM decision" not in s["key_find"]
        assert "No order submitted" not in s["key_find"]


def test_progressive_summary_running_through_step_3():
    """A run with status='running' and calls through step 3:
    - Steps 1-2 are 'done' with real content (or neutral completed placeholder).
    - Step 3 is 'active' with real trader response or in-progress placeholder.
    - Steps 4-5 are 'pending' with empty key_find.
    """
    run = {
        "id": "run-prog-step-3",
        "ticker": "NVDA",
        "status": "running",
    }
    llm_calls = [
        # Step 1 calls
        {
            "seq": 1,
            "node": "Market Analyst",
            "kind": "llm",
            "ok": True,
            "response": "Uptrend with strong momentum.",
            "ts": "2026-09-21T10:00:00Z",
        },
        # Step 2 calls
        {
            "seq": 2,
            "node": "Bull Researcher",
            "kind": "llm",
            "ok": True,
            "response": "Data center demand continues accelerating.",
            "ts": "2026-09-21T10:01:00Z",
        },
        {
            "seq": 3,
            "node": "Research Manager",
            "kind": "llm",
            "ok": True,
            "response": "Consensus favors upside given supply constraints.",
            "ts": "2026-09-21T10:02:00Z",
        },
        # Step 3 calls
        {
            "seq": 4,
            "node": "Trader",
            "kind": "llm",
            "ok": True,
            "response": "Proposing 5% position with stop loss at support.",
            "ts": "2026-09-21T10:03:00Z",
        },
    ]

    summary = summarize_run(run, llm_calls=llm_calls)
    steps = summary["steps"]

    # Step 1: done
    assert steps[0]["n"] == 1
    assert steps[0]["state"] == "done"
    assert "Uptrend with strong momentum" in steps[0]["key_find"]

    # Step 2: done
    assert steps[1]["n"] == 2
    assert steps[1]["state"] == "done"
    assert "Consensus favors upside given supply constraints." in steps[1]["key_find"]
    assert "Debated growth catalysts against valuation" not in steps[1]["key_find"]

    # Step 3: active (Trader call response used)
    assert steps[2]["n"] == 3
    assert steps[2]["state"] == "active"
    assert "Proposing 5% position with stop loss" in steps[2]["key_find"]

    # Steps 4 and 5: pending
    assert steps[3]["n"] == 4
    assert steps[3]["state"] == "pending"
    assert steps[3]["key_find"] == ""
    assert "PM decision" not in steps[3]["key_find"]

    assert steps[4]["n"] == 5
    assert steps[4]["state"] == "pending"
    assert steps[4]["key_find"] == ""
    assert "No order submitted" not in steps[4]["key_find"]


def test_progressive_summary_running_through_step_4():
    """A run with status='running' and calls through step 4 (Risk/PM):
    - Steps 1-3 are 'done'.
    - Step 4 is 'active'.
    - Step 5 is 'pending' because no order/recommendation has been stored yet.
    """
    run = {
        "id": "run-prog-step-4",
        "ticker": "AMZN",
        "status": "running",
    }
    llm_calls = [
        {"seq": 1, "node": "Market Analyst", "kind": "llm", "ok": True, "response": "Market data", "ts": "2026-09-21T10:00:00Z"},
        {"seq": 2, "node": "Research Manager", "kind": "llm", "ok": True, "response": "Research consensus", "ts": "2026-09-21T10:01:00Z"},
        {"seq": 3, "node": "Trader", "kind": "llm", "ok": True, "response": "Trader sizing", "ts": "2026-09-21T10:02:00Z"},
        {"seq": 4, "node": "Portfolio Manager", "kind": "llm", "ok": True, "response": "Approved allocation of 4%.", "ts": "2026-09-21T10:03:00Z"},
    ]

    summary = summarize_run(run, llm_calls=llm_calls)
    steps = summary["steps"]

    assert steps[0]["state"] == "done"
    assert steps[1]["state"] == "done"
    assert steps[2]["state"] == "done"
    assert steps[3]["state"] == "active"
    assert "Approved allocation of 4%." in steps[3]["key_find"]
    assert steps[4]["state"] == "pending"
    assert steps[4]["key_find"] == ""


def test_progressive_summary_running_step_5_reached_with_order():
    """When an order record is present while running, step 5 is recognized."""
    run = {
        "id": "run-prog-step-5",
        "ticker": "TSLA",
        "status": "running",
        "order": {
            "status": "submitted",
            "qty": 15,
            "order_type": "bracket",
            "limit_price": "240.00",
            "stop_price": "230.00",
            "take_profit_price": "260.00",
            "alpaca_order_id": "alpaca-ord-123",
        },
    }
    llm_calls = [
        {"seq": 1, "node": "Market Analyst", "kind": "llm", "ok": True, "response": "Market data", "ts": "2026-09-21T10:00:00Z"},
        {"seq": 2, "node": "Research Manager", "kind": "llm", "ok": True, "response": "RM data", "ts": "2026-09-21T10:01:00Z"},
        {"seq": 3, "node": "Trader", "kind": "llm", "ok": True, "response": "Trader data", "ts": "2026-09-21T10:02:00Z"},
        {"seq": 4, "node": "Portfolio Manager", "kind": "llm", "ok": True, "response": "PM data", "ts": "2026-09-21T10:03:00Z"},
    ]

    summary = summarize_run(run, llm_calls=llm_calls)
    steps = summary["steps"]

    assert steps[0]["state"] == "done"
    assert steps[1]["state"] == "done"
    assert steps[2]["state"] == "done"
    assert steps[3]["state"] == "done"
    assert steps[4]["state"] == "done"
    assert "Submitted 15-share BRACKET" in steps[4]["key_find"]
    assert "alpaca-ord-123" in steps[4]["key_find"]


def test_progressive_summary_advisory_in_progress_vs_completed():
    """Advisory run in progress (no completed_at) uses progressive step states.
    Advisory run that completed (completed_at set) keeps completed behavior.
    """
    # In-progress advisory
    run_advisory_in_progress = {
        "id": "run-adv-prog",
        "ticker": "SPY",
        "status": "advisory",
        # completed_at is None or missing
    }
    sum_prog = summarize_run(run_advisory_in_progress, llm_calls=[])
    assert sum_prog["steps"][0]["state"] == "active"
    assert sum_prog["steps"][1]["state"] == "pending"

    # Completed advisory
    run_advisory_completed = {
        "id": "run-adv-comp",
        "ticker": "SPY",
        "status": "advisory",
        "completed_at": "2026-09-21T10:15:00Z",
    }
    sum_comp = summarize_run(run_advisory_completed, llm_calls=[])
    # Completed runs do not mark steps as pending
    for s in sum_comp["steps"]:
        assert s.get("state") != "pending"

