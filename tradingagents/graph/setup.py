# TradingAgents/graph/setup.py

import functools
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from tradingagents.agents import (
    create_aggressive_debator,
    create_bear_researcher,
    create_bull_researcher,
    create_conservative_debator,
    create_fundamentals_analyst,
    create_market_analyst,
    create_msg_delete,
    create_neutral_debator,
    create_news_analyst,
    create_portfolio_manager,
    create_research_manager,
    create_sentiment_analyst,
    create_trader,
)
from tradingagents.agents.utils.agent_states import AgentState
from tradingagents.llm_clients.llm_trace import LLMSpy, ToolNodeSpy, set_current_node

from .analyst_execution import build_analyst_execution_plan
from .conditional_logic import ConditionalLogic

# Every target a shared conditional router can return. Each edge driven by the
# router maps all of them, so a fall-through return (e.g. under prompt/i18n/
# refactor drift in the speaker labels) can never hit a missing path_map entry
# and crash LangGraph mid-run (#1088).
DEBATE_PATH_MAP = {
    "Bull Researcher": "Bull Researcher",
    "Bear Researcher": "Bear Researcher",
    "Research Manager": "Research Manager",
}
RISK_ANALYSIS_PATH_MAP = {
    "Aggressive Analyst": "Aggressive Analyst",
    "Conservative Analyst": "Conservative Analyst",
    "Neutral Analyst": "Neutral Analyst",
    "Portfolio Manager": "Portfolio Manager",
}


class GraphSetup:
    """Handles the setup and configuration of the agent graph."""

    def __init__(
        self,
        quick_thinking_llm: Any,
        deep_thinking_llm: Any,
        tool_nodes: dict[str, ToolNode],
        conditional_logic: ConditionalLogic,
    ):
        """Initialize with required components."""
        self.quick_thinking_llm = quick_thinking_llm
        self.deep_thinking_llm = deep_thinking_llm
        self.tool_nodes = tool_nodes
        self.conditional_logic = conditional_logic

    def setup_graph(
        self, selected_analysts=("market", "social", "news", "fundamentals")
    ):
        """Set up and compile the agent workflow graph.

        Args:
            selected_analysts (list): List of analyst types to include. Options are:
                - "market": Market analyst
                - "social": Social media analyst
                - "news": News analyst
                - "fundamentals": Fundamentals analyst
        """
        plan = build_analyst_execution_plan(selected_analysts)

        tracing_quick = LLMSpy(self.quick_thinking_llm, tier="quick", label="quick_thinking_llm")
        tracing_deep = LLMSpy(self.deep_thinking_llm, tier="deep", label="deep_thinking_llm")

        analyst_factories = {
            "market": lambda: create_market_analyst(tracing_quick),
            "social": lambda: create_sentiment_analyst(tracing_quick),
            "news": lambda: create_news_analyst(tracing_quick),
            "fundamentals": lambda: create_fundamentals_analyst(tracing_quick),
        }

        # Create researcher and manager nodes
        bull_researcher_node = create_bull_researcher(tracing_quick)
        bear_researcher_node = create_bear_researcher(tracing_quick)
        research_manager_node = create_research_manager(tracing_deep)
        trader_node = create_trader(tracing_quick)

        # Create risk analysis nodes
        aggressive_analyst = create_aggressive_debator(tracing_quick)
        neutral_analyst = create_neutral_debator(tracing_quick)
        conservative_analyst = create_conservative_debator(tracing_quick)
        portfolio_manager_node = create_portfolio_manager(tracing_deep)

        # Create workflow
        workflow = StateGraph(AgentState)

        def _wrap_node(node_name, fn):
            @functools.wraps(fn)
            def _wrapped(state, *args, **kwargs):
                set_current_node(node_name)
                return fn(state, *args, **kwargs)
            return _wrapped

        # Add analyst nodes to the graph
        for spec in plan.specs:
            workflow.add_node(spec.agent_node, _wrap_node(spec.agent_node, analyst_factories[spec.key]()))
            workflow.add_node(spec.clear_node, create_msg_delete())
            workflow.add_node(spec.tool_node, ToolNodeSpy(self.tool_nodes[spec.key]))

        # Add other nodes
        workflow.add_node("Bull Researcher", _wrap_node("Bull Researcher", bull_researcher_node))
        workflow.add_node("Bear Researcher", _wrap_node("Bear Researcher", bear_researcher_node))
        workflow.add_node("Research Manager", _wrap_node("Research Manager", research_manager_node))
        workflow.add_node("Trader", _wrap_node("Trader", trader_node))
        workflow.add_node("Aggressive Analyst", _wrap_node("Aggressive Analyst", aggressive_analyst))
        workflow.add_node("Neutral Analyst", _wrap_node("Neutral Analyst", neutral_analyst))
        workflow.add_node("Conservative Analyst", _wrap_node("Conservative Analyst", conservative_analyst))
        workflow.add_node("Portfolio Manager", _wrap_node("Portfolio Manager", portfolio_manager_node))

        # Define edges
        # Start with the first analyst
        workflow.add_edge(START, plan.specs[0].agent_node)

        # Connect analysts in sequence
        for i, spec in enumerate(plan.specs):
            current_analyst = spec.agent_node
            current_tools = spec.tool_node
            current_clear = spec.clear_node

            # Add conditional edges for current analyst
            workflow.add_conditional_edges(
                current_analyst,
                getattr(self.conditional_logic, f"should_continue_{spec.key}"),
                [current_tools, current_clear],
            )
            workflow.add_edge(current_tools, current_analyst)

            # Connect to next analyst or to Bull Researcher if this is the last analyst
            if i < len(plan.specs) - 1:
                workflow.add_edge(current_clear, plan.specs[i + 1].agent_node)
            else:
                workflow.add_edge(current_clear, "Bull Researcher")

        # Both research-debate edges share the complete DEBATE_PATH_MAP (#1088).
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                self.conditional_logic.should_continue_debate,
                DEBATE_PATH_MAP,
            )
        workflow.add_edge("Research Manager", "Trader")
        workflow.add_edge("Trader", "Aggressive Analyst")
        # All three risk edges share the complete RISK_ANALYSIS_PATH_MAP (#1088).
        for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
            workflow.add_conditional_edges(
                risk_node,
                self.conditional_logic.should_continue_risk_analysis,
                RISK_ANALYSIS_PATH_MAP,
            )

        workflow.add_edge("Portfolio Manager", END)

        return workflow
