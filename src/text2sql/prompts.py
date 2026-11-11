"""提示词模板与解析工具。

模板用 string.Template（$var），避免 JSON 示例里的花括号与 str.format 冲突。
所有模板都是中文，结构与 PDF 的 Prompt 一致，并补上了 PDF 遗漏的"失败 SQL"。
"""

from __future__ import annotations

import json
import re
from string import Template
from typing import Any, Iterable

# ---------------------------------------------------------------- 改写 / 实体抽取
REWRITE_PROMPT = Template("""你是企业数据查询助手。请把用户的自然语言问题改写为标准的查询描述，
并抽取后续检索需要的线索。不要写 SQL。

【今天】：$today
【用户问题】：$query

只输出如下 JSON（不要代码块、不要解释）：
{
  "rewritten_query": "一句话标准查询描述，含时间、维度、指标、筛选、排序",
  "sub_questions": ["若问题需要拆解，列出子问题；否则为空数组"],
  "entities": ["问题中出现的业务实体：地名、品类、品牌、人名等"],
  "column_keywords": ["可能对应的字段语义词，如 销售额/金额/销量/城市"]
}""")

# ---------------------------------------------------------------- Analyzer：执行计划
PLANNER_PROMPT = Template("""你是一个资深的数据库架构师。请根据用户问题、业务知识和表结构，制定 SQL 查询逻辑步骤。

【用户问题】：$query
【改写后的问题】：$rewritten_query
【今天】：$today
【数据库方言】：$dialect
【方言约束】：
$dialect_rules
【业务知识】：
$business_knowledge
【已知取值映射】：
$value_mappings
【相关表结构】：
$schema

请按以下格式输出规划（切勿直接写出完整 SQL，只写逻辑）：
1. 确定主表与关联表：...
2. 过滤条件：...（时间范围、状态、地区等，时间以【今天】为基准计算）
3. 聚合与分组策略：...
4. 排序与限制：...
5. 需要注意的坑：...（软删除、编码值、方言函数等）""")

# ---------------------------------------------------------------- Coder：生成 SQL
CODER_PROMPT = Template("""你是 $dialect 方言的 SQL 工程师。请严格依据执行计划，写出**一条**可执行的查询。

硬性规则：
1. 只能使用【表结构】里出现的表和字段，禁止编造字段名。
2. 时间相关条件以【今天】为基准计算（例如"上个月"）。
3. 若【已知取值映射】与你的直觉冲突，以映射为准。
4. 遵守【方言约束】。
5. 只输出一个 ```sql 代码块，不要解释。

【用户问题】：$query
【今天】：$today
【执行计划】：
$plan
【已知取值映射】：
$value_mappings
【方言约束】：
$dialect_rules
【表结构】：
$schema""")

# ---------------------------------------------------------------- Coder：报错修复
REPAIR_PROMPT = Template("""你刚才生成的 SQL 在执行时报错了。请参考以下信息进行修正。

【数据库类型】：$dialect
【原始问题】：$query
【执行计划】：
$plan
【已知取值映射】：
$value_mappings
【表结构】：
$schema
【你刚才生成的 SQL】：
$failed_sql
【数据库报错】：
$error_msg
【历史尝试】：
$history

请先反思报错原因（是否引用了不存在的列？是否缺少聚合？类型/方言不对？），
然后输出修正后的 SQL（只输出一个 ```sql 代码块，不要解释）。""")

# ---------------------------------------------------------------- Evaluator：择优
JUDGE_PROMPT = Template("""你是 SQL 评审员。用户问题如下，多个候选 SQL 都执行成功了，但结果不一致。
请判断哪一个候选最符合用户意图。

【用户问题】：$query
【改写后的问题】：$rewritten_query
【执行计划】：
$plan

【候选】：
$candidates

只输出 JSON（不要代码块）：{"choice": 1, "reason": "一句话理由"}""")

# ---------------------------------------------------------------- 解析工具
_SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_sql(text: str) -> str:
    """从模型输出里取出 SQL：优先代码块，其次第一条 SELECT/WITH。"""
    blocks = [m.strip() for m in _SQL_BLOCK.findall(text) if m.strip()]
    if blocks:
        return blocks[0].rstrip(";").strip() + ";"
    match = re.search(r"(?is)\b(select|with)\b.+", text)
    if match:
        return match.group(0).strip().rstrip(";").strip() + ";"
    raise ValueError(f"模型输出中没有找到 SQL: {text[:120]}")


def extract_json(text: str) -> dict[str, Any]:
    """从模型输出里取出第一个 JSON 对象（容忍代码块和前后解释）。"""
    cleaned = _SQL_BLOCK.sub(lambda m: m.group(1), text).strip()
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(cleaned[start:end + 1])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    raise ValueError(f"模型输出中没有合法 JSON: {text[:120]}")


# ---------------------------------------------------------------- 片段渲染
def render_list(items: Iterable[str], bullet: str = "- ") -> str:
    lines = [f"{bullet}{item}" for item in items if str(item).strip()]
    return "\n".join(lines) if lines else "（无）"


def render_business_knowledge(chunks: Iterable[dict[str, str]]) -> str:
    parts = [f"- 【{c.get('title', '')}】{c.get('text', '')}".strip()
             for c in chunks if c.get("text")]
    return "\n".join(parts) if parts else "（无）"


def render_value_mappings(mappings: Iterable[Any]) -> str:
    lines = []
    for m in mappings:
        if isinstance(m, dict):
            lines.append(f"- {m.get('prompt') or _mapping_from_dict(m)}")
        else:
            lines.append(f"- {m.to_prompt()}")
    return "\n".join(lines) if lines else "（无）"


def _mapping_from_dict(m: dict[str, Any]) -> str:
    value = m.get("value", "")
    column_type = m.get("column_type", "text")
    numeric = column_type in {"integer", "bigint", "real", "double", "decimal"}
    literal = str(value) if numeric else f"'{value}'"
    return f'"{m.get("entity", "")}" -> {m.get("table")}.{m.get("column")} = {literal}'


def render_candidates(candidates: Iterable[dict[str, Any]]) -> str:
    rows = []
    for index, c in enumerate(candidates, start=1):
        preview = c.get("rows") or []
        rows.append(
            f"候选 {index}：\n```sql\n{c.get('sql', '')}\n```\n"
            f"列：{c.get('columns', [])}\n结果预览：{preview[:5]}"
        )
    return "\n\n".join(rows) if rows else "（无）"
