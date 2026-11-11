"""LangGraph 编排：Router → Analyzer → Coder → Executor →（Evaluator | 修复 | 失败）。"""

from __future__ import annotations

from datetime import date

from langgraph.graph import END, StateGraph

from text2sql.nodes.analyzer import make_analyzer_node
from text2sql.nodes.coder import make_coder_node
from text2sql.nodes.evaluator import make_evaluator_node
from text2sql.nodes.executor import make_executor_node
from text2sql.nodes.router import make_router_node
from text2sql.runtime import Runtime
from text2sql.state import Text2SQLState, event, initial_state


def make_fail_node():
    def fail_node(state: Text2SQLState) -> dict:
        return {
            "status": "failed",
            "error": state.get("last_error", "达到最大修复轮次仍失败"),
            "trace": [event("graph", "达到最大修复轮次，放弃",
                            rounds=state.get("round", 0))],
        }

    return fail_node


def make_route_after_executor(runtime: Runtime):
    def route(state: Text2SQLState) -> str:
        if any(c.get("ok") for c in state.get("candidates", [])):
            return "evaluate"
        if state.get("round", 0) <= runtime.settings.max_repair_rounds:
            return "repair"
        return "failed"

    return route


def build_graph(runtime: Runtime):
    workflow = StateGraph(Text2SQLState)
    workflow.add_node("router", make_router_node(runtime))
    workflow.add_node("analyzer", make_analyzer_node(runtime))
    workflow.add_node("coder", make_coder_node(runtime))
    workflow.add_node("executor", make_executor_node(runtime))
    workflow.add_node("evaluator", make_evaluator_node(runtime))
    workflow.add_node("give_up", make_fail_node())

    workflow.set_entry_point("router")
    workflow.add_edge("router", "analyzer")
    workflow.add_edge("analyzer", "coder")
    workflow.add_edge("coder", "executor")
    workflow.add_conditional_edges(
        "executor",
        make_route_after_executor(runtime),
        {"evaluate": "evaluator", "repair": "coder", "failed": "give_up"},
    )
    workflow.add_edge("evaluator", END)
    workflow.add_edge("give_up", END)
    return workflow.compile()


def run_query(runtime: Runtime, query: str, today: date | None = None) -> Text2SQLState:
    return build_graph(runtime).invoke(initial_state(query, today))
