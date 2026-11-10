"""BM25 稀疏检索：jieba 中文分词 + rank_bm25。

表级、列级、业务文档三处共用同一个 BM25Index，索引内容由调用方决定。
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

import jieba
from rank_bm25 import BM25Okapi

jieba.setLogLevel(logging.ERROR)

_NON_WORD = re.compile(r"^[\W_]+$", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """中文分词：小写化、去掉纯标点与空白。"""
    tokens = (t.lower().strip() for t in jieba.lcut(text))
    return [t for t in tokens if t and not _NON_WORD.match(t)]


class BM25Index:
    def __init__(self, docs: dict[str, str]):
        self._ids = list(docs)
        self._corpus = [tokenize(docs[i]) for i in self._ids]
        usable = [doc for doc in self._corpus if doc]
        self._bm25 = BM25Okapi(self._corpus) if usable else None

    def __len__(self) -> int:
        return len(self._ids)

    def search(self, query: str, top_k: int | None = None) -> list[tuple[str, float]]:
        if self._bm25 is None or not self._ids:
            return []
        tokens = set(tokenize(query))
        if not tokens:
            return []
        # 小语料里 BM25 的 idf 可能恰好为 0，因此用"至少命中一个查询词"来判断相关，
        # 再按 BM25 分数排序，避免把真正命中的文档过滤掉。
        scores = self._bm25.get_scores(list(tokens))
        ranked = sorted(
            ((self._ids[i], float(scores[i])) for i, doc in enumerate(self._corpus)
             if tokens & set(doc)),
            key=lambda kv: -kv[1],
        )
        return ranked if top_k is None else ranked[:top_k]
