"""Tests verifying market order default and 1-week short-term focus across all agents.

As required by REQUIRE_market_order_and_1week.md:
- Trader defaults to market order (no entry_price unless significantly better level).
- TraderProposal schema description reflects market-order execution as default.
- All 10 agents include 1-week / 5 trading days horizon instructions.
- build_order creates a MARKET order when entry_price is None.
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from langchain_core.messages import AIMessage

from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from tradingagents.agents.analysts.market_analyst import create_market_analyst
from tradingagents.agents.analysts.news_analyst import create_news_analyst
import tradingagents.agents.analysts.sentiment_analyst as sentiment
from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
from tradingagents.agents.managers.research_manager import create_research_manager
from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
from tradingagents.agents.schemas import (
    PortfolioDecision,
    PortfolioRating,
    ResearchPlan,
    SentimentBand,
    SentimentReport,
    TraderAction,
    TraderProposal,
)
from tradingagents.agents.trader.trader import create_trader
from webapp.execution import build_order


def _capturing_llm(captured: dict, result):
    """LLM whose structured binding records the prompt it was handed."""
    structured = MagicMock()
    structured.invoke.side_effect = lambda prompt: (
        captured.__setitem__("prompt", prompt) or result
    )
    llm = MagicMock()
    llm.with_structured_output.return_value = structured
    return llm


def _prompt_text(prompt) -> str:
    """Flatten a captured prompt (str, message list, ChatPromptValue, or objects) to text."""
    if isinstance(prompt, str):
        return prompt
    if hasattr(prompt, "to_messages"):
        prompt = prompt.to_messages()
    parts = []
    for m in prompt:
        parts.append(m.get("content", "") if isinstance(m, dict) else getattr(m, "content", ""))
    return "\n".join(str(p) for p in parts)


# ---------------------------------------------------------------------------
# 1. Trader & Schema: Market Order Default
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_trader_proposal_schema_entry_price_description():
    """Verify TraderProposal.entry_price description mentions market-order default."""
    desc = TraderProposal.model_fields["entry_price"].description or ""
    assert "Omit for market-order execution (the default)" in desc
    assert "significantly better price" in desc


@pytest.mark.unit
def test_trader_prompt_market_order_default_wording():
    """Verify Trader prompt instructs the model to default to market order and close in 5 days."""
    captured = {}
    llm = _capturing_llm(captured, TraderProposal(action=TraderAction.BUY, reasoning="test"))
    create_trader(llm)({
        "company_of_interest": "NVDA",
        "investment_plan": "**Recommendation**: Buy",
        "market_report": "Support at $170; resistance at $195.",
        "portfolio_context": "",
    })
    prompt = _prompt_text(captured["prompt"])
    assert "By default, do NOT provide an entry price — the execution engine will use a market order" in prompt
    assert "significantly better level" in prompt
    assert "1 week" in prompt
    assert "5 trading days" in prompt


# ---------------------------------------------------------------------------
# 2. One-Week Short-Term Focus Across All Agents
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_market_analyst_prompt_1week_focus():
    from langchain_core.runnables import RunnableLambda

    captured = {}

    class MockLLM:
        def bind_tools(self, tools):
            return RunnableLambda(
                lambda prompt_val: captured.__setitem__("prompt", prompt_val) or AIMessage(content="Report", tool_calls=[])
            )

    create_market_analyst(MockLLM())({
        "company_of_interest": "NVDA",
        "trade_date": "2026-01-15",
        "messages": [],
    })
    prompt = _prompt_text(captured["prompt"])
    assert "1-week (5 trading days) price action" in prompt
    assert "1-week holding period" in prompt


@pytest.mark.unit
def test_sentiment_analyst_prompt_1week_focus(monkeypatch):
    monkeypatch.setattr(sentiment, "fetch_stocktwits_messages", lambda *a, **k: "st")
    monkeypatch.setattr(sentiment, "fetch_reddit_posts", lambda *a, **k: "rd")
    monkeypatch.setattr(sentiment.get_news, "func", lambda *a, **k: "news", raising=False)

    captured = {}
    llm = _capturing_llm(captured, SentimentReport(
        overall_band=SentimentBand.BULLISH, overall_score=7.0,
        confidence="high", narrative="narrative",
    ))
    sentiment.create_sentiment_analyst(llm)({
        "company_of_interest": "NVDA", "trade_date": "2026-01-15",
        "asset_type": "stock", "messages": [],
    })
    prompt = _prompt_text(captured["prompt"])
    assert "The trader's time horizon is 1 week" in prompt
    assert "next 5 trading days" in prompt


@pytest.mark.unit
def test_news_analyst_prompt_1week_focus():
    from langchain_core.runnables import RunnableLambda

    captured = {}

    class MockLLM:
        def bind_tools(self, tools):
            return RunnableLambda(
                lambda prompt_val: captured.__setitem__("prompt", prompt_val) or AIMessage(content="News report", tool_calls=[])
            )

    create_news_analyst(MockLLM())({
        "company_of_interest": "NVDA",
        "trade_date": "2026-01-15",
        "messages": [],
    })
    prompt = _prompt_text(captured["prompt"])
    assert "past week that are likely to impact the stock within the next 5 trading days" in prompt
    assert "Prioritize imminent catalysts" in prompt


@pytest.mark.unit
def test_fundamentals_analyst_prompt_1week_focus():
    from langchain_core.runnables import RunnableLambda

    captured = {}

    class MockLLM:
        def bind_tools(self, tools):
            return RunnableLambda(
                lambda prompt_val: captured.__setitem__("prompt", prompt_val) or AIMessage(content="Fundamentals report", tool_calls=[])
            )

    create_fundamentals_analyst(MockLLM())({
        "company_of_interest": "NVDA",
        "trade_date": "2026-01-15",
        "messages": [],
    })
    prompt = _prompt_text(captured["prompt"])
    assert "impact the stock within a 1-week horizon" in prompt
    assert "most recent quarter's performance" in prompt


@pytest.mark.unit
def test_bull_researcher_prompt_1week_focus():
    captured = {}

    class MockLLM:
        def invoke(self, prompt_str):
            captured["prompt"] = prompt_str
            return AIMessage(content="Bull argument")

    create_bull_researcher(MockLLM())({
        "company_of_interest": "NVDA",
        "investment_debate_state": {"count": 0},
        "market_report": "", "sentiment_report": "", "news_report": "", "fundamentals_report": "",
    })
    prompt = _prompt_text(captured["prompt"])
    assert "Time horizon: The trader plans to hold for approximately 1 week." in prompt
    assert "within 5 trading days" in prompt


@pytest.mark.unit
def test_bear_researcher_prompt_1week_focus():
    captured = {}

    class MockLLM:
        def invoke(self, prompt_str):
            captured["prompt"] = prompt_str
            return AIMessage(content="Bear argument")

    create_bear_researcher(MockLLM())({
        "company_of_interest": "NVDA",
        "investment_debate_state": {"count": 0},
        "market_report": "", "sentiment_report": "", "news_report": "", "fundamentals_report": "",
    })
    prompt = _prompt_text(captured["prompt"])
    assert "Time horizon: The trader plans to hold for approximately 1 week." in prompt
    assert "within 5 trading days" in prompt


@pytest.mark.unit
def test_research_manager_prompt_1week_focus():
    captured = {}
    llm = _capturing_llm(
        captured,
        ResearchPlan(recommendation=PortfolioRating.BUY, rationale="r", strategic_actions="s"),
    )
    create_research_manager(llm)({
        "company_of_interest": "NVDA",
        "investment_debate_state": {"count": 0, "history": "debate history"},
    })
    prompt = _prompt_text(captured["prompt"])
    assert "Time horizon: This is a short-term (1 week) trading decision." in prompt
    assert "actionable within 5 trading days" in prompt


@pytest.mark.unit
def test_portfolio_manager_prompt_1week_focus():
    captured = {}
    llm = _capturing_llm(
        captured,
        PortfolioDecision(rating=PortfolioRating.BUY, executive_summary="e", investment_thesis="i"),
    )
    create_portfolio_manager(llm)({
        "company_of_interest": "NVDA",
        "portfolio_context": "",
        "risk_debate_state": {
            "history": "h", "aggressive_history": "", "conservative_history": "", "neutral_history": "",
            "current_aggressive_response": "", "current_conservative_response": "", "current_neutral_response": "",
            "count": 0,
        },
        "investment_plan": "plan",
        "trader_investment_plan": "trader plan",
    })
    prompt = _prompt_text(captured["prompt"])
    assert "Time horizon: 1 week (5 trading days)." in prompt
    assert "short-term trade expected to resolve within a week" in prompt


@pytest.mark.unit
@pytest.mark.parametrize("creator", [
    create_aggressive_debator,
    create_conservative_debator,
    create_neutral_debator,
])
def test_risk_debaters_prompt_1week_focus(creator):
    captured = {}

    class MockLLM:
        def invoke(self, prompt_str):
            captured["prompt"] = prompt_str
            return AIMessage(content="Risk argument")

    creator(MockLLM())({
        "company_of_interest": "NVDA",
        "portfolio_context": "",
        "risk_debate_state": {"count": 0},
        "market_report": "", "sentiment_report": "", "news_report": "", "fundamentals_report": "",
        "trader_investment_plan": "plan",
    })
    prompt = _prompt_text(captured["prompt"])
    assert "Time horizon: This is a 1-week trade. Evaluate risk/reward on that timeframe, not a multi-month investment horizon." in prompt


# ---------------------------------------------------------------------------
# 3. Order Builder Execution Behavior
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_build_order_market_order_when_entry_price_none():
    """Verify build_order produces a market order when entry_price is None or omitted."""
    # Buy with entry_price explicitly None
    decision_none = {"action": "Buy", "entry_price": None}
    order = build_order("AAPL", decision_none, 100000, current_price=150.0)
    assert order is not None
    assert order["otype"] == "market"
    assert order["limit_price"] is None
    assert order["side"] == "buy"
    assert order["notional"] == 5000.0  # 5% default

    # Buy with entry_price omitted
    decision_omitted = {"action": "Buy"}
    order = build_order("AAPL", decision_omitted, 100000, current_price=150.0)
    assert order is not None
    assert order["otype"] == "market"
    assert order["limit_price"] is None

    # Sell with entry_price None
    decision_sell = {"action": "Sell", "entry_price": None}
    order = build_order("AAPL", decision_sell, 100000, current_price=150.0, held_qty=20)
    assert order is not None
    assert order["otype"] == "market"
    assert order["limit_price"] is None
    assert order["side"] == "sell"
    assert order["qty"] == 20


@pytest.mark.unit
def test_build_order_limit_order_when_entry_price_provided():
    """Verify build_order produces a limit order when entry_price is explicitly given within 25%."""
    decision = {"action": "Buy", "entry_price": 142.50}
    order = build_order("AAPL", decision, 100000, current_price=150.0)
    assert order is not None
    assert order["otype"] == "limit"
    assert order["limit_price"] == 142.50
    assert order["side"] == "buy"
