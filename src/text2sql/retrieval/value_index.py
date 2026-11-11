"""值级检索（实体对齐）：把用户说的"上海""男""华东区"映射到库里的编码值。

PDF 里用 Elasticsearch 建枚举值倒排；演示规模改为进程内条目表 + rapidfuzz 模糊匹配，
接口保持 ValueIndex.match(entities) -> [ValueMapping]，日后可换 ES。

两条来源：
    1. 各表的低基数列（城市名、省份、大区、品类、品牌……），自动找出对应编码
    2. 列注释里的枚举对（如 gender 注释"1=男，2=女"）
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import duckdb
from difflib import SequenceMatcher

from text2sql.catalog import DatabaseInfo

_TEXT_TYPES = {"text", "varchar", "char"}
_NUMERIC_TYPES = {"integer", "bigint", "smallint", "real", "double", "decimal",
                  "numeric", "boolean"}
_ENUM_PAIR = re.compile(r"(\d+)\s*[=:：]\s*([^\s，,；;、/=]+)")
_TEXT_ENUM_PAIR = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*[=:：]\s*([^\s，,；;、/=]+)")


@dataclass(frozen=True)
class ValueEntry:
    table: str
    match_text: str      # 用户可能说出的词（如 上海 / 男 / 华东）
    column: str          # 写进 SQL 的列
    value: str           # 写进 SQL 的值
    source_column: str   # 匹配来源列，仅用于说明
    source_value: str    # 匹配来源值
    column_type: str = "text"  # 用于决定 SQL 字面量是否加引号


@dataclass(frozen=True)
class ValueMapping:
    entity: str
    table: str
    column: str
    value: str
    matched: str
    score: float
    column_type: str = "text"

    def sql_literal(self) -> str:
        # 按列类型决定引号：021 这种文本编码必须加引号，枚举数字不加
        if self.column_type in _NUMERIC_TYPES:
            return self.value
        return f"'{self.value}'"

    def to_prompt(self) -> str:
        return f'"{self.entity}" -> {self.table}.{self.column} = {self.sql_literal()}'


def _connect(db: DatabaseInfo):
    path = db.descriptor.path
    if db.dialect == "sqlite":
        return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    if db.dialect == "duckdb":
        return duckdb.connect(str(path), read_only=True)
    raise ValueError(f"不支持的值检索方言: {db.dialect}")


def _fetch(con, sql: str) -> list[tuple]:
    cur = con.execute(sql)
    return cur.fetchall()


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class ValueIndex:
    def __init__(self, entries: list[ValueEntry]):
        self.entries = entries

    @classmethod
    def build(cls, db: DatabaseInfo, *, max_distinct: int = 200,
              limit: int = 500) -> "ValueIndex":
        entries: list[ValueEntry] = []
        con = _connect(db)
        try:
            for table in db.tables.values():
                total = _fetch(con, f"SELECT COUNT(*) FROM {_q(table.name)}")[0][0]
                if total == 0:
                    continue
                pks = table.primary_keys
                pk = pks[0] if len(pks) == 1 else None
                for col in table.columns:
                    if col.name in pks:
                        continue
                    if col.simple_type in _TEXT_TYPES:
                        # 外键 / 编码列是"被引用"的一方，不能反过来当语义值
                        # （否则 sales_record.city_code='021' 会被映射到流水ID）
                        lower = col.name.lower()
                        if col.foreign_key or lower.endswith(("_code", "_id")):
                            continue
                        key_type = (table.column(pk).simple_type
                                    if pk else col.simple_type)
                        entries.extend(cls._text_column_entries(
                            con, table, col, pk, key_type,
                            max_distinct, limit))
                    for number, label in _ENUM_PAIR.findall(col.comment or ""):
                        entries.append(ValueEntry(
                            table=table.name, match_text=label, column=col.name,
                            value=number, source_column=col.name, source_value=number,
                            column_type=col.simple_type))
        finally:
            con.close()
        return cls(cls._dedupe(entries))

    @staticmethod
    def _text_column_entries(con, table, column_info, pk: str | None,
                             key_type: str, max_distinct: int,
                             limit: int) -> list[ValueEntry]:
        table, column = table.name, column_info.name
        total, distinct = _fetch(
            con, f"SELECT COUNT({_q(column)}), COUNT(DISTINCT {_q(column)}) "
                 f"FROM {_q(table)}")[0]
        if not distinct or distinct > max_distinct:
            return []
        if pk and pk != column and distinct == total:
            # 值唯一：映射到主键编码（如 上海 -> city_code='021'）
            rows = _fetch(con, f"SELECT {_q(column)}, CAST({_q(pk)} AS VARCHAR) "
                               f"FROM {_q(table)} WHERE {_q(column)} IS NOT NULL "
                               f"LIMIT {limit}")
            return [ValueEntry(table=table, match_text=str(value), column=pk,
                               value=str(key), source_column=column,
                               source_value=str(value), column_type=key_type)
                    for value, key in rows]
        # 值不唯一（如 region_name='华东' 对应多个省份）：直接等值过滤该列
        rows = _fetch(con, f"SELECT DISTINCT {_q(column)} FROM {_q(table)} "
                           f"WHERE {_q(column)} IS NOT NULL LIMIT {limit}")
        values = {str(value) for (value,) in rows}
        entries = [ValueEntry(table=table, match_text=value, column=column,
                              value=value, source_column=column,
                              source_value=value, column_type="text")
                   for value in values]
        # 注释里的 code=中文 对，如 event_type 注释 "click=点击"
        for code, label in _TEXT_ENUM_PAIR.findall(column_info.comment or ""):
            if code.lower() in {v.lower() for v in values}:
                entries.append(ValueEntry(
                    table=table, match_text=label, column=column, value=code,
                    source_column=column, source_value=code, column_type="text"))
        return entries

    @staticmethod
    def _dedupe(entries: list[ValueEntry]) -> list[ValueEntry]:
        seen: set[tuple] = set()
        out = []
        for e in entries:
            key = (e.table, e.match_text, e.column, e.value)
            if key not in seen:
                seen.add(key)
                out.append(e)
        return out

    def match(self, entities: Iterable[str], *, top_k: int = 1,
              score_cutoff: float = 70.0) -> list[ValueMapping]:
        results: list[ValueMapping] = []
        for entity in entities:
            text = str(entity).strip()
            if not text:
                continue
            scored: list[tuple[float, ValueEntry]] = []
            for entry in self.entries:
                score = SequenceMatcher(None, text, entry.match_text).ratio() * 100
                if text in entry.match_text or entry.match_text in text:
                    score = max(score, 90.0)
                if score >= score_cutoff:
                    scored.append((score, entry))
            scored.sort(key=lambda kv: -kv[0])
            taken: set[tuple] = set()
            for score, entry in scored:
                key = (entry.table, entry.column, entry.value)
                if key in taken:
                    continue
                taken.add(key)
                results.append(ValueMapping(
                    entity=text, table=entry.table, column=entry.column,
                    value=entry.value, matched=entry.match_text,
                    score=round(score, 1), column_type=entry.column_type))
                if len(taken) >= top_k:
                    break
        return results

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([asdict(e) for e in self.entries],
                                   ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "ValueIndex":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls([ValueEntry(**e) for e in raw])
