"""极简向量索引：归一化向量上的点积检索，落盘为 json + npy。

演示规模（千级文本）用 numpy 矩阵乘即可，不引入 FAISS；
接口保持 search(query_vec) -> [(id, score)]，将来换 FAISS 不影响调用方。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

from text2sql.retrieval.embedder import Embedder


@dataclass
class DenseIndex:
    ids: list[str]
    texts: list[str]
    vectors: np.ndarray  # (n, dim)，已归一化

    @classmethod
    def build(cls, ids: Sequence[str], texts: Sequence[str],
              embedder: Embedder) -> "DenseIndex":
        vectors = embedder.encode(list(texts))
        return cls(ids=list(ids), texts=list(texts),
                   vectors=np.asarray(vectors, dtype=np.float32))

    def search(self, query_vec: np.ndarray, top_k: int | None = None
               ) -> list[tuple[str, float]]:
        if not self.ids:
            return []
        scores = self.vectors @ np.asarray(query_vec, dtype=np.float32)
        order = np.argsort(-scores, kind="stable")
        if top_k is not None:
            order = order[:top_k]
        return [(self.ids[i], float(scores[i])) for i in order]

    def subset(self, keep: set[str]) -> "DenseIndex":
        rows = [i for i, doc_id in enumerate(self.ids) if doc_id in keep]
        return DenseIndex(
            ids=[self.ids[i] for i in rows],
            texts=[self.texts[i] for i in rows],
            vectors=self.vectors[rows],
        )

    def save(self, directory: str | Path, name: str = "index") -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.json").write_text(
            json.dumps({"ids": self.ids, "texts": self.texts}, ensure_ascii=False),
            encoding="utf-8")
        np.save(directory / f"{name}.npy", self.vectors)

    @classmethod
    def load(cls, directory: str | Path, name: str = "index") -> "DenseIndex":
        directory = Path(directory)
        raw = json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
        return cls(ids=raw["ids"], texts=raw["texts"],
                   vectors=np.load(directory / f"{name}.npy"))
