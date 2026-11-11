"""业务知识库：把指标字典 Markdown 按小节切块，建 向量 + BM25 双路索引。

业务口径（如"高净值客户"= 资产 ≥ 100 万）本身不是 SQL，也不在 Schema 里，
必须作为独立知识源注入 Analyzer 的 prompt。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from text2sql.retrieval.bm25 import BM25Index
from text2sql.retrieval.dense import DenseIndex
from text2sql.retrieval.embedder import Embedder
from text2sql.retrieval.fusion import rrf


@dataclass(frozen=True)
class KBChunk:
    title: str
    text: str


def split_markdown(markdown: str) -> list[KBChunk]:
    """按二级标题切块；没有二级标题时整篇作为一块。"""
    parts = re.split(r"^##\s+", markdown, flags=re.MULTILINE)
    chunks: list[KBChunk] = []
    for part in parts[1:]:
        lines = part.splitlines()
        title = lines[0].strip()
        body = "\n".join(lines[1:]).strip()
        if title:
            chunks.append(KBChunk(title=title, text=f"【{title}】\n{body}"))
    return chunks


class BusinessKB:
    def __init__(self, chunks: list[KBChunk], index: DenseIndex):
        self.chunks = chunks
        self.index = index
        self._by_title = {c.title: c for c in chunks}
        self._bm25 = BM25Index({c.title: c.text for c in chunks})

    @classmethod
    def build(cls, markdown_path: str | Path, embedder: Embedder) -> "BusinessKB":
        chunks = split_markdown(Path(markdown_path).read_text(encoding="utf-8"))
        index = DenseIndex.build([c.title for c in chunks],
                                 [c.text for c in chunks], embedder)
        return cls(chunks, index)

    def search(self, query: str, embedder: Embedder, *,
               top_k: int = 3) -> list[tuple[KBChunk, float]]:
        dense = self.index.search(embedder.encode([query])[0], top_k=None)
        sparse = self._bm25.search(query, top_k=None)
        fused = rrf([[i for i, _ in dense], [i for i, _ in sparse]])[:top_k]
        return [(self._by_title[title], score) for title, score in fused]

    def save(self, directory: str | Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.index.save(directory, "index")
        (directory / "chunks.json").write_text(
            json.dumps([asdict(c) for c in self.chunks], ensure_ascii=False),
            encoding="utf-8")

    @classmethod
    def load(cls, directory: str | Path) -> "BusinessKB":
        directory = Path(directory)
        chunks = [KBChunk(**c) for c in json.loads(
            (directory / "chunks.json").read_text(encoding="utf-8"))]
        return cls(chunks, DenseIndex.load(directory, "index"))
