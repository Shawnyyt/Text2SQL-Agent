"""Executor 节点：沙盒执行全部候选，产出结果/报错，并记录本轮尝试。

未知列报错时执行 PDF Q2 的"防丢策略"：把保留表的全量列重新放回 schema，
给下一轮修复更大的字段视野。
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from text2sql.runtime import Runtime
from text2sql.sandbox import Sandbox
from text2sql.state import Attempt, Text2SQLState, event

_UNKNOWN_COLUMN = ("no such column", "unknown column", "invalid identifier",
                   "does not exist", "unknown column name")


def _jsonable(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def make_executor_node(runtime: Runtime):
    def executor_node(state: Text2SQLState) -> dict:
        db = runtime.databases[state["db_id"]]
        candidates = state.get("candidates", [])
        sandbox = Sandbox(db.descriptor.path, db.dialect,
                          allowed_tables=set(db.tables))
        results = []
        if not candidates:
            results = [{"sql": "", "ok": False,
                        "error": "模型未产出可解析的 SQL",
                        "columns": [], "rows": [], "elapsed_ms": 0.0}]
        for candidate in candidates:
            result = sandbox.execute(candidate.get("sql", ""))
            results.append({
                "sql": result.sql,
                "ok": result.ok,
                "columns": list(result.columns),
                "rows": [[_jsonable(v) for v in row] for row in result.rows],
                "error": result.error,
                "elapsed_ms": result.elapsed_ms,
            })

        attempt_round = len(state.get("attempts", []))
        errors = [r["error"] for r in results if not r["ok"] and r["error"]]
        failed = [r for r in results if not r["ok"]]
        update: dict = {
            "candidates": results,
            "attempts": [Attempt(round=attempt_round,
                                 sqls=[r["sql"] for r in results],
                                 errors=errors)],
            "round": attempt_round + 1,
            "last_error": failed[-1]["error"] if failed else "",
            "last_failed_sql": failed[-1]["sql"] if failed else "",
            "trace": [event("executor", f"第 {attempt_round} 轮执行完成",
                            ok=len(results) - len(failed), failed=len(failed),
                            errors=errors)],
        }

        if (failed and state.get("seeds")
                and any(token in " ".join(errors).lower()
                        for token in _UNKNOWN_COLUMN)):
            relaxed = "\n\n".join(
                db.table(name).render()
                for name in state["seeds"] if name in db.tables)
            if len(relaxed) > len(state.get("schema_text", "")):
                update["schema_text"] = relaxed
                update["trace"].append(event(
                    "executor", "检测到未知列，已放宽 Schema（补全保留表字段）",
                    tokens_before=len(state.get("schema_text", "")),
                    tokens_after=len(relaxed)))
        return update

    return executor_node
