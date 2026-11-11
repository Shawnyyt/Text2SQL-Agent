"""CP8：全局状态与提示词模板 / 解析工具。"""

from datetime import date

import pytest

from text2sql.prompts import (
    CODER_PROMPT,
    JUDGE_PROMPT,
    PLANNER_PROMPT,
    REPAIR_PROMPT,
    REWRITE_PROMPT,
    extract_json,
    extract_sql,
    render_business_knowledge,
    render_candidates,
    render_list,
    render_value_mappings,
)
from text2sql.state import event, initial_state


# ---------------- 状态 ----------------

def test_initial_state_defaults():
    s = initial_state("  上个月华东区 Top3  ")
    assert s["query"] == "上个月华东区 Top3"
    assert s["today"] == date.today().isoformat()
    assert s["attempts"] == [] and s["trace"] == [] and s["candidates"] == []
    assert s["round"] == 0


def test_initial_state_with_today_override():
    s = initial_state("q", today=date(2026, 11, 8))
    assert s["today"] == "2026-11-08"


def test_event_shape():
    e = event("analyzer", "压缩完成", tokens=226)
    assert e == {"node": "analyzer", "message": "压缩完成",
                 "detail": {"tokens": 226}}


# ---------------- SQL / JSON 解析 ----------------

def test_extract_sql_from_fenced_block():
    assert extract_sql("说明\n```sql\nSELECT 1\n```\n后记") == "SELECT 1;"
    assert extract_sql("```\nWITH t AS (SELECT 1) SELECT * FROM t\n```") \
        == "WITH t AS (SELECT 1) SELECT * FROM t;"


def test_extract_sql_from_plain_text_and_multiple_blocks():
    assert extract_sql("答案是 SELECT a FROM t") == "SELECT a FROM t;"
    assert extract_sql("```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```") == "SELECT 1;"


def test_extract_sql_without_sql_raises():
    with pytest.raises(ValueError, match="没有找到 SQL"):
        extract_sql("我无法回答这个问题。")


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"choice": 2}\n```') == {"choice": 2}
    assert extract_json('结果如下：{"choice": 1, "reason": "ok"} 完毕') == {
        "choice": 1, "reason": "ok"}


def test_extract_json_invalid_raises():
    with pytest.raises(ValueError, match="没有合法 JSON"):
        extract_json("没有 JSON")


# ---------------- 模板渲染 ----------------

def _planner_fields() -> dict:
    return {
        "query": "上个月华东区卖得最好的三个爆款",
        "rewritten_query": "2026-10 华东区销量 Top3 商品",
        "today": "2026-11-08",
        "dialect": "sqlite",
        "dialect_rules": "- 日期用 strftime",
        "business_knowledge": "- 【爆款】销量排名",
        "value_mappings": "- \"华东\" -> dim_region.region_name = '华东'",
        "schema": "Table sales_record (销售流水):\n- quantity (integer): 销售件数",
    }


def test_planner_prompt_contains_pdf_sections():
    text = PLANNER_PROMPT.substitute(_planner_fields())
    for section in ["确定主表与关联表", "过滤条件", "聚合与分组策略", "排序与限制"]:
        assert section in text
    assert "切勿直接写出完整 SQL" in text
    assert "dim_region.region_name" in text


def test_coder_and_repair_prompts_include_failed_sql():
    base = {"query": "q", "today": "2026-11-08", "plan": "p",
            "value_mappings": "（无）", "dialect_rules": "- x", "schema": "S"}
    coder = CODER_PROMPT.substitute(dialect="sqlite", **base)
    assert "只能使用" in coder and "```sql" in coder

    repair = REPAIR_PROMPT.substitute(dialect="sqlite", **base,
                                      failed_sql="SELECT bad",
                                      error_msg="no such column: bad",
                                      history="-")
    assert "SELECT bad" in repair
    assert "no such column: bad" in repair


def test_rewrite_and_judge_prompts():
    rewrite = REWRITE_PROMPT.substitute(query="上海的订单", today="2026-11-08")
    assert "rewritten_query" in rewrite and "entities" in rewrite
    judge = JUDGE_PROMPT.substitute(query="q", rewritten_query="q", plan="p",
                                    candidates="候选 1：...")
    assert '"choice"' in judge


# ---------------- 片段渲染 ----------------

def test_render_helpers():
    assert render_list(["a", "b"]) == "- a\n- b"
    assert render_list([]) == "（无）"
    assert render_business_knowledge([{"title": "活跃用户", "text": "近 30 天登录"}]) \
        == "- 【活跃用户】近 30 天登录"
    assert render_business_knowledge([]) == "（无）"


def test_render_value_mappings_accepts_dict_and_object():
    class M:
        def to_prompt(self):
            return '"上海" -> dim_city.city_code = \'021\''

    assert render_value_mappings([M()]) == '- "上海" -> dim_city.city_code = \'021\''
    rendered = render_value_mappings([{"entity": "男", "table": "users",
                                       "column": "gender", "value": "1",
                                       "column_type": "integer"}])
    assert rendered == '- "男" -> users.gender = 1'


def test_render_candidates_contains_sql_and_preview():
    text = render_candidates([{"sql": "SELECT 1", "columns": ["a"],
                               "rows": [["x"]]}])
    assert "候选 1" in text and "SELECT 1" in text and "结果预览" in text
