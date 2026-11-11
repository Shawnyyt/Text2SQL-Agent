"""Coder 节点：首轮多候并发生成，后续轮次带报错修复。"""

from __future__ import annotations

from text2sql.llm import user_msg
from text2sql.prompts import (
    CODER_PROMPT,
    REPAIR_PROMPT,
    extract_sql,
    render_value_mappings,
)
from text2sql.runtime import Runtime
from text2sql.state import Attempt, Text2SQLState, event


def _history(state: Text2SQLState) -> str:
    lines = []
    for attempt in state.get("attempts", []):
        lines.append(f"第 {attempt.get('round', 0) + 1} 轮：")
        for sql in attempt.get("sqls", []):
            lines.append(f"- {sql}")
        for err in attempt.get("errors", []):
            lines.append(f"  错误：{err}")
    return "\n".join(lines) if lines else "（无）"


def make_coder_node(runtime: Runtime):
    def coder_node(state: Text2SQLState) -> dict:
        db = runtime.databases[state["db_id"]]
        settings = runtime.settings
        mappings = state.get("value_mappings", [])
        attempt_round = len(state.get("attempts", []))
        sqls: list[str] = []
        parse_errors: list[str] = []

        if attempt_round == 0:
            # 首轮：高温并发生成多条候选（PDF：T=0.7, top_p=0.9）
            prompt = CODER_PROMPT.substitute(
                query=state["query"],
                today=state["today"],
                plan=state.get("plan", ""),
                value_mappings=render_value_mappings(mappings),
                dialect_rules=state.get("dialect_rules", ""),
                dialect=db.dialect,
                schema=state.get("schema_text", ""),
            )
            raw_list = runtime.coder_llm.sample(
                user_msg(prompt), n=settings.candidate_count,
                temperature=0.7, top_p=0.9)
            for raw in raw_list:
                try:
                    sqls.append(extract_sql(raw))
                except ValueError as exc:
                    parse_errors.append(str(exc))
            message = f"首轮生成 {len(sqls)} 条候选"
        else:
            # 修复轮：低温稳定收敛，携带失败 SQL + 报错 + 历史
            prompt = REPAIR_PROMPT.substitute(
                query=state["query"],
                today=state["today"],
                plan=state.get("plan", ""),
                value_mappings=render_value_mappings(mappings),
                dialect_rules=state.get("dialect_rules", ""),
                dialect=db.dialect,
                schema=state.get("schema_text", ""),
                failed_sql=state.get("last_failed_sql", ""),
                error_msg=state.get("last_error", ""),
                history=_history(state),
            )
            raw = runtime.coder_llm.chat(user_msg(prompt), temperature=0.2)
            try:
                sqls.append(extract_sql(raw))
            except ValueError as exc:
                parse_errors.append(str(exc))
            message = f"第 {attempt_round} 轮修复生成 {len(sqls)} 条候选"

        unique = list(dict.fromkeys(s for s in sqls if s.strip()))
        return {
            "candidates": [{"sql": s, "ok": False} for s in unique],
            "trace": [event("coder", message, round=attempt_round,
                            sqls=unique, parse_errors=parse_errors)],
        }

    return coder_node


def empty_attempt(round_no: int, error: str) -> Attempt:
    return Attempt(round=round_no, sqls=[], errors=[error])
