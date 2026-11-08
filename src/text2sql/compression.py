"""智能 Schema 压缩（PDF 的 Smart Compression）。

流程：
    1. 种子：多粒度检索选出的表 / 列
    2. 主键补全：每张种子表补上主键
    3. 血缘补全：在外键图上找种子表之间的最短路径，把桥接表和关联键补回来
    4. 裁剪渲染：只输出命中列 + 主外键，剔除索引、默认值、字符集等无关元数据
       （这些信息在 catalog 阶段就没有进入结构化元数据，因此不需要再解析原始 DDL）

输出是 PDF 中的极简 DDL 文本，并给出压缩前后的 token 估算。
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from text2sql.catalog import DatabaseInfo

# 一个外键边：(源表, 源列, 目标表, 目标列)
Edge = tuple[str, str, str, str]


@dataclass(frozen=True)
class CompressionResult:
    schema_text: str
    tables: dict[str, tuple[str, ...]]  # 表名 -> 保留的列（按表内原始顺序）
    full_tokens: int
    compressed_tokens: int

    @property
    def ratio(self) -> float:
        """压缩后 token / 全量 token。"""
        return self.compressed_tokens / self.full_tokens if self.full_tokens else 1.0

    @property
    def summary(self) -> str:
        return (f"压缩 {self.full_tokens:,} → {self.compressed_tokens:,} tokens"
                f"（保留 {self.ratio:.1%}）")


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中日韩字符约 1.5 字/token，其余字符约 4 字符/token。

    仅用于展示压缩效果，不追求与具体 tokenizer 完全一致。
    """
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = sum(1 for ch in text if not ch.isspace() and not "一" <= ch <= "鿿")
    return max(1, math.ceil(cjk / 1.5 + other / 4))


def build_fk_graph(db: DatabaseInfo) -> dict[str, list[Edge]]:
    """无向外键图：表的每条外键边在两个方向上各登记一次。"""
    adj: dict[str, list[Edge]] = {name: [] for name in db.tables}
    for table in db.tables.values():
        for col in table.foreign_keys:
            ref_table, ref_col = col.foreign_key.split(".", 1)
            if ref_table not in adj:
                continue
            adj[table.name].append((table.name, col.name, ref_table, ref_col))
            adj[ref_table].append((ref_table, ref_col, table.name, col.name))
    return adj


def shortest_path(adj: dict[str, list[Edge]], start: str,
                  goal: str) -> list[Edge] | None:
    """BFS 求两张表之间的最短外键路径（含两端），返回沿途的边。"""
    if start == goal:
        return []
    prev: dict[str, Edge] = {}
    seen = {start}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for edge in adj.get(node, []):
            nxt = edge[2]
            if nxt in seen:
                continue
            seen.add(nxt)
            prev[nxt] = edge
            if nxt == goal:
                path = []
                cur = goal
                while cur != start:
                    e = prev[cur]
                    path.append(e)
                    cur = e[0]
                return path[::-1]
            queue.append(nxt)
    return None


def compress_schema(
    db: DatabaseInfo,
    seeds: dict[str, list[str] | None],
    *,
    add_primary_keys: bool = True,
    add_bridges: bool = True,
) -> CompressionResult:
    """按种子表/列压缩 schema。

    seeds: 表名 -> 命中列；值为 None 表示该表保留全部列。
    """
    for table_name in seeds:
        db.table(table_name)  # 表不存在时立刻报错

    kept: dict[str, list[str]] = {}  # 保持插入顺序：种子表在前，桥接表在后
    for table_name, columns in seeds.items():
        table = db.table(table_name)
        if columns is None:
            kept[table_name] = [c.name for c in table.columns]
        else:
            missing = set(columns) - {c.name for c in table.columns}
            if missing:
                raise KeyError(f"{table_name} 中不存在列: {sorted(missing)}")
            kept[table_name] = list(columns)

    if add_bridges and len(seeds) > 1:
        adj = build_fk_graph(db)
        seed_names = list(seeds)
        for i, left in enumerate(seed_names):
            for right in seed_names[i + 1:]:
                path = shortest_path(adj, left, right)
                if path is None:
                    continue
                for src, src_col, dst, dst_col in path:
                    kept.setdefault(src, []).append(src_col)
                    kept.setdefault(dst, []).append(dst_col)

    if add_primary_keys:
        for table_name in kept:
            kept[table_name].extend(db.table(table_name).primary_keys)

    # 已保留的表之间的外键，两端列都要在（保证 JOIN 可写）
    for table_name in list(kept):
        for col in db.table(table_name).foreign_keys:
            ref_table, ref_col = col.foreign_key.split(".", 1)
            if ref_table in kept:
                kept[table_name].append(col.name)
                kept[ref_table].append(ref_col)

    # 去重并按表内原始列顺序排列
    ordered: dict[str, tuple[str, ...]] = {}
    for table_name, cols in kept.items():
        wanted = set(cols)
        ordered[table_name] = tuple(
            c.name for c in db.table(table_name).columns if c.name in wanted
        )

    schema_text = "\n\n".join(
        db.table(t).render(list(cols)) for t, cols in ordered.items()
    )
    full_text = db.render()
    return CompressionResult(
        schema_text=schema_text,
        tables=ordered,
        full_tokens=estimate_tokens(full_text),
        compressed_tokens=estimate_tokens(schema_text),
    )
