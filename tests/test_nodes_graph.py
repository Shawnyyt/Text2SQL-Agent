"""CP9：Router / Analyzer 节点与 LangGraph 编排（FakeLLM + 哈希向量，全离线）。"""

import json

from text2sql.graph import build_graph
from text2sql.nodes.analyzer import make_analyzer_node
from text2sql.nodes.router import make_router_node
from text2sql.runtime import Runtime
from text2sql.state import initial_state

class FakeLLM:
    """第一次调用返回改写 JSON，其余返回固定计划。"""

    def __init__(self, rewrite, plan: str = "1. 主表 users"):
        self.rewrite = rewrite
        self.plan = plan
        self.prompts: list[str] = []

    def chat(self, messages, *, temperature=0.0, top_p=1.0):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "只输出如下 JSON" in prompt:
            return (self.rewrite if isinstance(self.rewrite, str)
                    else json.dumps(self.rewrite, ensure_ascii=False))
        return self.plan

    def sample(self, messages, *, n, temperature, top_p=1.0):
        return [self.plan] * n



def make_runtime(parts, rewrite: dict | str, plan: str = "计划", settings=None):
    analyzer = FakeLLM(rewrite, plan)
    runtime = Runtime(
        settings=settings or parts["settings"],
        embedder=parts["embedder"], analyzer_llm=analyzer,
        coder_llm=FakeLLM(rewrite, plan),
        descriptors=parts["descriptors"], databases=parts["databases"],
        router=parts["router"], business_kb=parts["business_kb"],
        retrievers=parts["retrievers"], value_indexes=parts["value_indexes"],
        dialect_dir=parts["dialect_dir"],
    )
    return runtime, analyzer


def test_router_selects_matching_database(parts):
    runtime, _ = make_runtime(parts, {})
    state = make_router_node(runtime)(initial_state("昨天 APP 的点击转化率"))
    assert state["db_id"] == "logs"
    assert state["dialect"] == "duckdb"
    assert "CAST AS DOUBLE" in state["dialect_rules"]
    assert state["trace"][0]["node"] == "router"


def test_analyzer_builds_schema_mappings_and_plan(parts):
    rewrite = {
        "rewritten_query": "查询上海客户的销售额",
        "sub_questions": [],
        "entities": ["上海"],
        "column_keywords": ["销售额"],
    }
    runtime, analyzer = make_runtime(parts, rewrite, plan="1. 主表 sales_record")
    state = {"query": "上海销售额", "today": "2026-11-08",
             "db_id": "shop", "dialect": "sqlite", "dialect_rules": "- x"}
    out = make_analyzer_node(runtime)(state)

    assert out["rewritten_query"] == "查询上海客户的销售额"
    assert out["entities"] == ["上海"]
    assert out["plan"] == "1. 主表 sales_record"
    assert any(m["column"] == "city_code" and m["value"] == "021"
               for m in out["value_mappings"])
    assert "sales_record" in out["seeds"]
    assert "Table" in out["schema_text"]
    assert len(out["trace"]) >= 5
    # 规划 prompt 应包含业务知识与压缩后的 schema
    plan_prompt = analyzer.prompts[-1]
    assert "Table" in plan_prompt


def test_analyzer_injects_tables_referenced_by_value_mappings(parts):
    settings = parts["settings"].model_copy(update={"retrieval_top_tables": 1})
    rewrite = {"rewritten_query": "华东区销售额", "entities": ["华东"],
               "column_keywords": [], "sub_questions": []}
    runtime, _ = make_runtime(parts, rewrite, settings=settings)
    state = {"query": "华东区销售额", "today": "2026-11-08",
             "db_id": "shop", "dialect": "sqlite"}
    out = make_analyzer_node(runtime)(state)
    # 值映射指向 dim_region，即使 Top1 召回没命中，也要补进 seeds
    assert "dim_region" in out["seeds"]
    assert "region_name" in out["seeds"]["dim_region"]


def test_analyzer_falls_back_when_rewrite_json_is_invalid(parts):
    runtime, _ = make_runtime(parts, "这不是 JSON")
    state = {"query": "原始问题", "today": "2026-11-08",
             "db_id": "shop", "dialect": "sqlite"}
    out = make_analyzer_node(runtime)(state)
    assert out["rewritten_query"] == "原始问题"
    assert out["trace"][0]["detail"]["parse_error"]


def test_graph_runs_router_then_analyzer_and_merges_trace(parts):
    rewrite = {"rewritten_query": "查询活跃用户", "entities": [],
               "column_keywords": [], "sub_questions": []}
    runtime, _ = make_runtime(parts, rewrite, plan="计划内容")
    result = build_graph(runtime).invoke(
        initial_state("高净值客户有多少", today=None))
    assert result["db_id"] == "shop"
    assert result["plan"] == "计划内容"
    nodes = [e["node"] for e in result["trace"]]
    assert nodes[0] == "router" and "analyzer" in nodes
    assert len(result["trace"]) >= 6
    assert result["schema_text"].startswith("Table")
