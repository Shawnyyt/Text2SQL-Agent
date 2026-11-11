"""多粒度 Schema 检索：表级粗排（向量 + BM25，RRF）→ 列级精排。

输出就是 Schema 压缩的输入：
    seeds = {"sales_record": ["sale_date", "quantity", ...], ...}
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from text2sql.catalog import DatabaseInfo
from text2sql.retrieval.bm25 import BM25Index
from text2sql.retrieval.dense import DenseIndex
from text2sql.retrieval.embedder import Embedder
from text2sql.retrieval.fusion import rrf


@dataclass(frozen=True)
class SchemaRetrievalResult:
    seeds: dict[str, list[str]]
    tables: list[tuple[str, float]]
    columns: list[tuple[str, float]]

    @property
    def n_columns(self) -> int:
        return sum(len(v) for v in self.seeds.values())


class SchemaRetriever:
    def __init__(self, table_index: DenseIndex, column_index: DenseIndex):
        self.table_index = table_index
        self.column_index = column_index
        self.table_bm25 = BM25Index(dict(zip(table_index.ids, table_index.texts)))
        self.column_bm25 = BM25Index(dict(zip(column_index.ids, column_index.texts)))

    @staticmethod
    def table_doc(table_name: str, comment: str) -> str:
        return f"{table_name} {comment}".strip()

    @staticmethod
    def column_doc(table_name: str, column_name: str, comment: str) -> str:
        return f"{table_name}.{column_name} {column_name} {comment}".strip()

    @classmethod
    def build(cls, db: DatabaseInfo, embedder: Embedder) -> "SchemaRetriever":
        table_ids, table_texts = [], []
        column_ids, column_texts = [], []
        for table in db.tables.values():
            table_ids.append(table.name)
            table_texts.append(cls.table_doc(table.name, table.comment))
            for col in table.columns:
                column_ids.append(f"{table.name}.{col.name}")
                column_texts.append(
                    cls.column_doc(table.name, col.name, col.comment))
        return cls(
            DenseIndex.build(table_ids, table_texts, embedder),
            DenseIndex.build(column_ids, column_texts, embedder),
        )

    def search(self, query: str, embedder: Embedder, *,
               top_tables: int = 5, cols_per_table: int = 4,
               max_cols: int = 30,
               scope: set[str] | None = None) -> SchemaRetrievalResult:
        """scope: 业务域允许的表（描述符里的 tables）。为 None 时检索全部表。"""
        qvec = embedder.encode([query])[0]

        limit = top_tables * 2 if scope is None else None
        dense_t = [i for i, _ in self.table_index.search(qvec, limit)
                   if scope is None or i in scope]
        sparse_t = [i for i, _ in self.table_bm25.search(query, None)
                    if scope is None or i in scope]
        fused_t = rrf([dense_t, sparse_t])[:top_tables]
        recalled = [t for t, _ in fused_t]
        recalled_set = set(recalled)

        dense_c = [(i, s) for i, s in self.column_index.search(qvec)
                   if i.split(".", 1)[0] in recalled_set]
        sparse_c = [(i, s) for i, s in self.column_bm25.search(query)
                    if i.split(".", 1)[0] in recalled_set]
        fused_c = rrf([[i for i, _ in dense_c], [i for i, _ in sparse_c]])

        by_table: dict[str, list[str]] = {t: [] for t in recalled}
        kept: list[tuple[str, float]] = []
        for doc_id, score in fused_c:
            table, column = doc_id.split(".", 1)
            if len(by_table[table]) >= cols_per_table:
                continue
            by_table[table].append(column)
            kept.append((doc_id, score))
            if len(kept) >= max_cols:
                break

        # 保留所有召回表：即使一列都没命中，压缩阶段也会补主键，保证 JOIN 可写
        seeds = {t: by_table[t] for t in recalled}
        return SchemaRetrievalResult(seeds=seeds, tables=fused_t, columns=kept)

    def save(self, directory: str | Path) -> None:
        self.table_index.save(directory, "tables")
        self.column_index.save(directory, "columns")

    @classmethod
    def load(cls, directory: str | Path) -> "SchemaRetriever":
        return cls(DenseIndex.load(directory, "tables"),
                   DenseIndex.load(directory, "columns"))
