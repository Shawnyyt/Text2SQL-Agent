"""Evaluator 节点：通过的结果集一致性投票；不一致时 LLM 判优。"""

from __future__ import annotations

from text2sql.llm import user_msg
from text2sql.prompts import JUDGE_PROMPT, extract_json, render_candidates
from text2sql.runtime import Runtime
from text2sql.state import Text2SQLState, event


def _normalized(rows: list[list]) -> tuple:
    def value(v):
        return round(v, 6) if isinstance(v, float) else v
    return tuple(sorted(tuple(value(v) for v in row) for row in rows))


def make_evaluator_node(runtime: Runtime):
    def evaluator_node(state: Text2SQLState) -> dict:
        ok = [c for c in state.get("candidates", []) if c.get("ok")]
        if not ok:
            return {
                "status": "failed",
                "error": state.get("last_error", "没有成功执行的候选"),
                "trace": [event("evaluator", "无成功候选")],
            }

        groups: dict[tuple, list[dict]] = {}
        for candidate in ok:
            groups.setdefault(_normalized(candidate["rows"]), []).append(candidate)

        if len(groups) == 1:
            chosen = ok[0]
            reason = "候选结果一致"
        else:
            try:
                prompt = JUDGE_PROMPT.substitute(
                    query=state["query"],
                    rewritten_query=state.get("rewritten_query", state["query"]),
                    plan=state.get("plan", ""),
                    candidates=render_candidates(ok),
                )
                raw = runtime.analyzer_llm.chat(user_msg(prompt), temperature=0.0)
                data = extract_json(raw)
                index = int(data.get("choice", 1)) - 1
                chosen = ok[index] if 0 <= index < len(ok) else ok[0]
                reason = str(data.get("reason", ""))
            except Exception as exc:  # 判优失败退回多数票，保证链路不中断
                chosen = max(groups.values(), key=len)[0]
                reason = f"判优失败，按多数票选择: {exc}"

        return {
            "final_sql": chosen["sql"],
            "final_candidate": chosen,
            "status": "success",
            "error": "",
            "trace": [event("evaluator", "候选优选完成",
                            reason=reason, groups=len(groups),
                            final_sql=chosen["sql"])],
        }

    return evaluator_node
