"""库级语义路由：在多个数据库描述符之间做向量检索，选目标库。"""

from __future__ import annotations

from pathlib import Path

from text2sql.catalog import DatabaseDescriptor
from text2sql.retrieval.dense import DenseIndex
from text2sql.retrieval.embedder import Embedder


def router_doc(desc: DatabaseDescriptor) -> str:
    tables = " ".join(desc.business_tables)
    return f"{desc.db_id} {desc.engine} {desc.domain} {tables}"


class RouterIndex:
    def __init__(self, db_ids: list[str], index: DenseIndex):
        self.db_ids = db_ids
        self.index = index

    @classmethod
    def build(cls, descriptors: list[DatabaseDescriptor],
              embedder: Embedder) -> "RouterIndex":
        ids = [d.db_id for d in descriptors]
        texts = [router_doc(d) for d in descriptors]
        return cls(ids, DenseIndex.build(ids, texts, embedder))

    def search(self, query: str, embedder: Embedder, *,
               top_k: int = 3) -> list[tuple[str, float]]:
        return self.index.search(embedder.encode([query])[0], top_k)

    def save(self, directory: str | Path) -> None:
        self.index.save(directory, "index")

    @classmethod
    def load(cls, directory: str | Path) -> "RouterIndex":
        index = DenseIndex.load(directory, "index")
        return cls(index.ids, index)
