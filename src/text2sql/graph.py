"""LangGraph 编排。

当前阶段：Router → Analyzer → END。
Coder / Executor / Evaluator 在 CP10 加入，条件边与重试在 CP11 补齐。
"""

from __future__ import annotations

from datetime import date

from langgraph.graph import END, StateGraph

from text2sql.nodes.analyzer import make_analyzer_node
from text2sql.nodes.router import make_router_node
from text2sql.runtime import Runtime
from text2sql.state import Text2SQLState, initial_state


def build_graph(runtime: Runtime):
    workflow = StateGraph(Text2SQLState)
    workflow.add_node("router", make_router_node(runtime))
    workflow.add_node("analyzer", make_analyzer_node(runtime))
    workflow.set_entry_point("router")
    workflow.add_edge("router", "analyzer")
    workflow.add_edge("analyzer", END)
    return workflow.compile()


def run_query(runtime: Runtime, query: str, today: date | None = None) -> Text2SQLState:
    return build_graph(runtime).invoke(initial_state(query, today))
