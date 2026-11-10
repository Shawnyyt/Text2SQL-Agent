"""RRF（Reciprocal Rank Fusion）：融合多路检索结果。

score(d) = Σ 1 / (k + rank_i(d))，rank 从 1 开始。
只依赖各路的排名，不依赖分数量纲，正好适合 BM25 与向量检索这种分数量纲不同的场景。
"""

from __future__ import annotations

from typing import Sequence


def rrf(rankings: Sequence[Sequence[str]], *, k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    first_seen: dict[str, tuple[int, int]] = {}
    for route, ranking in enumerate(rankings):
        seen_in_route: set[str] = set()
        for rank, doc_id in enumerate(ranking, start=1):
            if doc_id in seen_in_route:  # 同一路里的重复项只算一次
                continue
            seen_in_route.add(doc_id)
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(doc_id, (route, rank))
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
