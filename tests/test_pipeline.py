"""CP10：Coder / Executor / Evaluator 与完整图（FakeLLM + 真实沙盒）。"""

import json

from text2sql.graph import build_graph
from text2sql.runtime import Runtime
from text2sql.state import initial_state


class ScriptedLLM:
    def __init__(self, rewrite, plan="计划", batch=None, repairs=None,
                 judge_choice=1):
        self.rewrite = rewrite
        self.plan = plan
        self.batch = list(batch or [])
        self.repairs = list(repairs or [])
        self.judge_choice = judge_choice
        self.sample_calls = 0
        self.repair_calls = 0
        self.judge_calls = 0
        self.prompts: list[str] = []

    def chat(self, messages, *, temperature=0.0, top_p=1.0):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "只输出如下 JSON" in prompt:
            return json.dumps(self.rewrite, ensure_ascii=False)
        if "SQL 评审员" in prompt:
            self.judge_calls += 1
            return json.dumps({"choice": self.judge_choice, "reason": "测试选择"})
        if "执行时报错" in prompt:
            self.repair_calls += 1
            return self.repairs.pop(0) if self.repairs else "SELECT 1"
        return self.plan

    def sample(self, messages, *, n, temperature, top_p=1.0):
        self.sample_calls += 1
        if len(self.batch) == 1:
            return self.batch * n
        return self.batch[:n]


REWRITE = {"rewritten_query": "有效用户数", "entities": [],
           "column_keywords": [], "sub_questions": []}


def make_runtime(parts, analyzer, coder, settings=None):
    return Runtime(
        settings=settings or parts["settings"], embedder=parts["embedder"],
        analyzer_llm=analyzer, coder_llm=coder,
        descriptors=parts["descriptors"], databases=parts["databases"],
        router=parts["router"], business_kb=parts["business_kb"],
        retrievers=parts["retrievers"], value_indexes=parts["value_indexes"],
        dialect_dir=parts["dialect_dir"],
    )


def run(parts, coder, analyzer=None, settings=None):
    analyzer = analyzer or ScriptedLLM(REWRITE)
    runtime = make_runtime(parts, analyzer, coder, settings)
    return build_graph(runtime).invoke(initial_state("有多少有效用户"))


def test_pipeline_success_in_one_round(parts):
    sql = "SELECT COUNT(*) AS n FROM users WHERE is_del = 0;"
    coder = ScriptedLLM(REWRITE, batch=[sql])
    state = run(parts, coder)
    assert state["status"] == "success"
    assert state["final_candidate"]["rows"] == [[2]]
    assert state["final_sql"] == sql
    assert len(state["attempts"]) == 1
    assert coder.sample_calls == 1 and coder.repair_calls == 0
    assert [e["node"] for e in state["trace"]][-1] == "evaluator"


def test_repair_after_unknown_column_relaxes_schema(parts):
    coder = ScriptedLLM(
        REWRITE,
        batch=["SELECT nickname FROM users;"],
        repairs=["SELECT COUNT(*) AS n FROM users WHERE is_del = 0;"])
    state = run(parts, coder)
    assert state["status"] == "success"
    assert len(state["attempts"]) == 2          # 首次 + 1 轮修复
    assert coder.repair_calls == 1
    messages = [e["message"] for e in state["trace"]]
    assert any("放宽 Schema" in m for m in messages)
    # 修复 prompt 里带上了失败 SQL 和数据库报错
    repair_prompt = next(p for p in coder.prompts if "执行时报错" in p)
    assert "SELECT nickname FROM users;" in repair_prompt
    assert "no such column" in repair_prompt.lower()


def test_all_rounds_fail_ends_with_failure(parts):
    coder = ScriptedLLM(
        REWRITE,
        batch=["SELECT nope FROM users;"],
        repairs=["SELECT nope2 FROM users;"] * 3)
    state = run(parts, coder)
    assert state["status"] == "failed"
    assert len(state["attempts"]) == 4          # 首次 + 3 轮修复
    assert state["round"] == 4
    assert coder.repair_calls == 3
    assert state["trace"][-1]["node"] == "graph"


def test_identical_results_skip_judge(parts):
    analyzer = ScriptedLLM(REWRITE)
    coder = ScriptedLLM(REWRITE, batch=[
        "SELECT COUNT(*) AS n FROM users WHERE is_del = 0;",
        "SELECT COUNT(user_id) AS n FROM users WHERE is_del = 0;"])
    state = run(parts, coder, analyzer)
    assert state["status"] == "success"
    assert analyzer.judge_calls == 0
    assert "结果一致" in state["trace"][-1]["detail"]["reason"]


def test_inconsistent_results_use_llm_judge(parts):
    analyzer = ScriptedLLM(REWRITE, judge_choice=3)
    coder = ScriptedLLM(REWRITE, batch=[
        "SELECT COUNT(*) AS n FROM users;",                    # 2
        "SELECT COUNT(*) AS n FROM users WHERE is_del = 1;",   # 0
        "SELECT COUNT(*) AS n FROM sales_record;"])            # 1
    state = run(parts, coder, analyzer)
    assert analyzer.judge_calls == 1
    assert state["status"] == "success"
    assert state["final_sql"] == "SELECT COUNT(*) AS n FROM sales_record;"
    assert state["trace"][-1]["detail"]["groups"] == 3


def test_judge_failure_falls_back_to_majority(parts):
    class BrokenJudge(ScriptedLLM):
        def chat(self, messages, *, temperature=0.0, top_p=1.0):
            if "SQL 评审员" in messages[-1]["content"]:
                self.judge_calls += 1
                return "无法判断"
            return super().chat(messages, temperature=temperature, top_p=top_p)

    analyzer = BrokenJudge(REWRITE)
    coder = ScriptedLLM(REWRITE, batch=[
        "SELECT COUNT(*) AS n FROM users;",
        "SELECT COUNT(*) AS n FROM users WHERE is_del = 1;"])
    state = run(parts, coder, analyzer)
    assert state["status"] == "success"
    assert "判优失败" in state["trace"][-1]["detail"]["reason"]
