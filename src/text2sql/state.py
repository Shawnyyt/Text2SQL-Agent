"""LangGraph 全局状态与数据结构。

状态在节点之间传递，每次节点返回的是"增量"（只覆盖自己负责的字段）；
attempts / trace 用 operator.add 累加，避免后一个节点覆盖掉前一个节点的记录。
"""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, TypedDict


class Candidate(TypedDict, total=False):
    """一条候选 SQL 及其执行结果。"""

    sql: str
    ok: bool
    columns: list[str]
    rows: list[list[Any]]   # 已转成可 JSON 序列化的值（日期等由 Executor 转字符串）
    error: str
    elapsed_ms: float


class Attempt(TypedDict, total=False):
    """一轮执行记录：首次生成或一次修复。"""

    round: int              # 0 = 首次生成，1..N = 第 N 次修复
    sqls: list[str]
    errors: list[str]


class TraceEvent(TypedDict, total=False):
    node: str
    message: str
    detail: dict[str, Any]


class Text2SQLState(TypedDict, total=False):
    # ---- 输入 ----
    query: str
    today: str                       # ISO 日期，prompt 中的“今天”

    # ---- Router ----
    db_id: str
    dialect: str
    dialect_rules: list[str]

    # ---- Analyzer：检索与知识 ----
    rewritten_query: str
    sub_questions: list[str]
    entities: list[str]              # 值检索的输入（上海 / 华东 / 男）
    column_keywords: list[str]       # 列级语义扩展词
    business_knowledge: list[dict[str, str]]  # [{title, text}]
    value_mappings: list[dict[str, Any]]      # [{entity, table, column, value, ...}]
    seeds: dict[str, list[str]]      # 压缩输入
    schema_text: str                 # 压缩输出
    plan: str                        # Execution Plan（伪代码/逻辑步骤）

    # ---- Coder / Executor ----
    candidates: list[Candidate]
    attempts: Annotated[list[Attempt], operator.add]
    round: int                       # 当前已完成轮次；0 表示尚未生成

    # ---- 收尾 ----
    final_sql: str
    final_candidate: Candidate
    status: str                      # success / failed
    error: str

    # ---- 全链路追踪（CLI 展示用）----
    trace: Annotated[list[TraceEvent], operator.add]


def initial_state(query: str, today: date | None = None) -> Text2SQLState:
    return Text2SQLState(
        query=query.strip(),
        today=(today or date.today()).isoformat(),
        attempts=[],
        trace=[],
        round=0,
        candidates=[],
    )


def event(node: str, message: str, **detail: Any) -> TraceEvent:
    return TraceEvent(node=node, message=message, detail=detail)
