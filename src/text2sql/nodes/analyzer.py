"""Analyzer 节点：改写与实体抽取 → 检索 → 补表 → 压缩 → 生成执行计划。"""

from __future__ import annotations

from dataclasses import asdict

from text2sql.compression import compress_schema
from text2sql.llm import user_msg
from text2sql.prompts import (
    PLANNER_PROMPT,
    REWRITE_PROMPT,
    extract_json,
    render_business_knowledge,
    render_value_mappings,
)
from text2sql.runtime import Runtime
from text2sql.state import Text2SQLState, event


def _as_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def make_analyzer_node(runtime: Runtime):
    def analyzer_node(state: Text2SQLState) -> dict:
        db = runtime.databases[state["db_id"]]
        settings = runtime.settings
        query, today = state["query"], state["today"]
        trace = []

        # 1) 改写 + 实体 + 字段扩展词
        raw = runtime.analyzer_llm.chat(
            user_msg(REWRITE_PROMPT.substitute(query=query, today=today)),
            temperature=0.1)
        parse_error = ""
        try:
            info = extract_json(raw)
        except ValueError as exc:
            info, parse_error = {}, str(exc)
        rewritten = str(info.get("rewritten_query") or query).strip()
        sub_questions = _as_list(info.get("sub_questions"))
        entities = _as_list(info.get("entities"))
        keywords = _as_list(info.get("column_keywords"))
        trace.append(event(
            "analyzer", "问题改写完成", rewritten=rewritten,
            entities=entities, column_keywords=keywords, parse_error=parse_error))

        # 2) 业务口径
        kb_hits = runtime.business_kb.search(
            rewritten, runtime.embedder,
            top_k=settings.retrieval_business_kb_top_k)
        knowledge = [{"title": chunk.title, "text": chunk.text}
                     for chunk, _ in kb_hits]
        trace.append(event("analyzer", "业务知识检索完成",
                           titles=[k["title"] for k in knowledge]))

        # 3) 值映射（实体对齐）
        mappings = runtime.value_indexes[db.db_id].match(entities)
        trace.append(event("analyzer", "值映射完成",
                           mappings=[m.to_prompt() for m in mappings]))

        # 4) 表/列召回
        retrieval_query = " ".join([rewritten, *keywords]).strip() or query
        result = runtime.retrievers[db.db_id].search(
            retrieval_query, runtime.embedder,
            top_tables=settings.retrieval_top_tables,
            cols_per_table=settings.retrieval_cols_per_table,
            max_cols=settings.retrieval_max_cols,
            scope=set(db.business_tables))
        seeds = {table: list(cols) for table, cols in result.seeds.items()}
        trace.append(event("analyzer", "Schema 召回完成",
                           tables=[t for t, _ in result.tables],
                           columns=result.n_columns))

        # 5) 值映射引用到的表补进 seeds（检索可能没召回，例如 dim_region）
        for mapping in mappings:
            if mapping.table in db.tables:
                cols = seeds.setdefault(mapping.table, [])
                if mapping.column not in cols:
                    cols.append(mapping.column)

        # 6) 压缩
        compressed = compress_schema(db, seeds)
        trace.append(event("analyzer", "Schema 压缩完成",
                           tokens=compressed.compressed_tokens,
                           full_tokens=compressed.full_tokens,
                           summary=compressed.summary))

        # 7) 执行计划（不写 SQL）
        plan_prompt = PLANNER_PROMPT.substitute(
            query=query,
            rewritten_query=rewritten,
            today=today,
            dialect=db.dialect,
            dialect_rules=state.get("dialect_rules", ""),
            business_knowledge=render_business_knowledge(knowledge),
            value_mappings=render_value_mappings(mappings),
            schema=compressed.schema_text,
        )
        plan = runtime.analyzer_llm.chat(user_msg(plan_prompt), temperature=0.1)
        trace.append(event("analyzer", "执行计划生成完成", chars=len(plan)))

        mapping_dicts = []
        for mapping in mappings:
            item = asdict(mapping)
            item["prompt"] = mapping.to_prompt()
            mapping_dicts.append(item)

        return {
            "rewritten_query": rewritten,
            "sub_questions": sub_questions,
            "entities": entities,
            "column_keywords": keywords,
            "business_knowledge": knowledge,
            "value_mappings": mapping_dicts,
            "seeds": {t: list(cols) for t, cols in compressed.tables.items()},
            "schema_text": compressed.schema_text,
            "plan": plan,
            "trace": trace,
        }

    return analyzer_node
