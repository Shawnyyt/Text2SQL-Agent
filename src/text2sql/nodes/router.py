"""Router 节点：语义路由到目标库，并加载该库的方言补丁。"""

from __future__ import annotations

from text2sql.runtime import Runtime
from text2sql.state import Text2SQLState, event


def make_router_node(runtime: Runtime):
    def router_node(state: Text2SQLState) -> dict:
        query = state["query"]
        hits = runtime.router.search(query, runtime.embedder, top_k=3)
        if not hits:
            raise RuntimeError("库路由失败：没有任何可用的数据库描述符")
        db_id, score = hits[0]
        descriptor = runtime.descriptors[db_id]
        return {
            "db_id": db_id,
            "dialect": descriptor.dialect,
            "dialect_rules": runtime.dialect_markdown(descriptor),
            "trace": [event(
                "router", f"路由到 {db_id}",
                score=round(score, 4),
                candidates=[{"db_id": d, "score": round(s, 4)} for d, s in hits],
            )],
        }

    return router_node
