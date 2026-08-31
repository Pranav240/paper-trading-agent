"""
Wires the four Phase 03 nodes into a LangGraph StateGraph.

Shape (fan-out, then fan-in, then a gate):

    START --> technical_analyst --\
                                    +--> portfolio_manager --> risk_manager --> END
    START --> sentiment_analyst --/

technical_analyst and sentiment_analyst both read only `symbol`/`as_of`
from state and each write to their own key (technical_opinion /
sentiment_opinion) — no write conflict, so LangGraph runs them
concurrently. See the note at the top of state.py for when that would
stop being true (it isn't, here).

Every dependency (data sources, repository, LLMs) is passed in rather
than constructed inside this function, for the same reason every node
factory takes its dependencies as arguments: it's what lets tests build
this graph with fakes/mocks instead of hitting Alpaca/OpenAI/Postgres.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.data_sources import HeadlineSource, PriceDataSource
from app.agent.portfolio_manager import make_portfolio_manager_node
from app.agent.risk_manager import make_risk_manager_node
from app.agent.sentiment_analyst import make_sentiment_analyst_node
from app.agent.state import GraphState
from app.agent.technical_analyst import make_technical_analyst_node
from app.repository.base import Repository


def build_decision_graph(
    *,
    price_source: PriceDataSource,
    headline_source: HeadlineSource,
    repository: Repository,
    sentiment_llm: ChatOpenAI | None = None,
    portfolio_llm: ChatOpenAI | None = None,
) -> CompiledStateGraph:
    graph = StateGraph(GraphState)

    graph.add_node("technical_analyst", make_technical_analyst_node(price_source))
    graph.add_node(
        "sentiment_analyst",
        make_sentiment_analyst_node(headline_source, llm=sentiment_llm),
    )
    graph.add_node("portfolio_manager", make_portfolio_manager_node(llm=portfolio_llm))
    graph.add_node("risk_manager", make_risk_manager_node(repository))

    graph.add_edge(START, "technical_analyst")
    graph.add_edge(START, "sentiment_analyst")
    graph.add_edge("technical_analyst", "portfolio_manager")
    graph.add_edge("sentiment_analyst", "portfolio_manager")
    graph.add_edge("portfolio_manager", "risk_manager")
    graph.add_edge("risk_manager", END)

    return graph.compile()
