"""索引构建：库路由、业务知识、各库的表/列/值索引。

CLI 的 `text2sql build-index` 与 scripts/build_index.py 共用这里的实现。
"""

from __future__ import annotations

import time
from pathlib import Path

from text2sql.catalog import DatabaseDescriptor, load_database
from text2sql.config import Settings, get_settings
from text2sql.retrieval.business_kb import BusinessKB
from text2sql.retrieval.db_router_index import RouterIndex
from text2sql.retrieval.embedder import Embedder, build_embedder
from text2sql.retrieval.schema_retriever import SchemaRetriever
from text2sql.retrieval.value_index import ValueIndex


def build_indexes(settings: Settings | None = None,
                  embedder: Embedder | None = None) -> dict[str, dict]:
    """构建全部索引并写盘，返回统计信息。"""
    s = settings or get_settings()
    data_dir: Path = s.data_dir
    index_root = data_dir / "index"
    desc_paths = sorted((data_dir / "descriptors").glob("*.json"))
    if not desc_paths:
        raise FileNotFoundError(f"未找到描述符: {data_dir / 'descriptors'}")
    embedder = embedder or build_embedder(s)
    stats: dict[str, dict] = {}

    started = time.monotonic()
    descriptors = [DatabaseDescriptor.load(p) for p in desc_paths]
    RouterIndex.build(descriptors, embedder).save(index_root / "router")
    stats["router"] = {"databases": len(descriptors),
                       "seconds": round(time.monotonic() - started, 1)}

    started = time.monotonic()
    kb = BusinessKB.build(data_dir / "knowledge" / "business_glossary.md", embedder)
    kb.save(index_root / "business")
    stats["business"] = {"chunks": len(kb.chunks),
                         "seconds": round(time.monotonic() - started, 1)}

    for path in desc_paths:
        started = time.monotonic()
        db = load_database(path)
        retriever = SchemaRetriever.build(db, embedder)
        retriever.save(index_root / db.db_id)
        values = ValueIndex.build(db)
        values.save(index_root / db.db_id / "values.json")
        stats[db.db_id] = {
            "tables": len(retriever.table_index.ids),
            "columns": len(retriever.column_index.ids),
            "values": len(values.entries),
            "seconds": round(time.monotonic() - started, 1),
        }
    stats["_index_dir"] = {"path": str(index_root)}
    return stats
