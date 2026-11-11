"""构建检索索引：库路由、业务知识、各库的表/列/值索引。

用法：python scripts/build_index.py
产物：data/index/（已在 .gitignore 中），可随时重建。
"""

from __future__ import annotations

import sys
import time

from text2sql.catalog import DatabaseDescriptor, load_database
from text2sql.config import get_settings
from text2sql.retrieval.business_kb import BusinessKB
from text2sql.retrieval.db_router_index import RouterIndex
from text2sql.retrieval.embedder import build_embedder
from text2sql.retrieval.schema_retriever import SchemaRetriever
from text2sql.retrieval.value_index import ValueIndex


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = get_settings()
    data_dir = settings.data_dir
    index_root = data_dir / "index"
    embedder = build_embedder(settings)
    desc_paths = sorted((data_dir / "descriptors").glob("*.json"))
    if not desc_paths:
        raise SystemExit(f"未找到描述符: {data_dir / 'descriptors'}")

    started = time.monotonic()
    descriptors = [DatabaseDescriptor.load(p) for p in desc_paths]
    RouterIndex.build(descriptors, embedder).save(index_root / "router")
    print(f"[router]   {len(descriptors)} 个库  {time.monotonic() - started:.1f}s")

    started = time.monotonic()
    kb = BusinessKB.build(data_dir / "knowledge" / "business_glossary.md", embedder)
    kb.save(index_root / "business")
    print(f"[business] {len(kb.chunks)} 个知识块  {time.monotonic() - started:.1f}s")

    for path in desc_paths:
        started = time.monotonic()
        db = load_database(path)
        retriever = SchemaRetriever.build(db, embedder)
        retriever.save(index_root / db.db_id)
        values = ValueIndex.build(db)
        values.save(index_root / db.db_id / "values.json")
        print(f"[{db.db_id}] 表 {len(retriever.table_index.ids)} / "
              f"列 {len(retriever.column_index.ids)} / 值 {len(values.entries)}  "
              f"{time.monotonic() - started:.1f}s")

    print(f"索引已写入 {index_root}")


if __name__ == "__main__":
    main()
