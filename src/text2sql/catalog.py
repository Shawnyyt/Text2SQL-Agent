"""元数据目录：从 SQLite / DuckDB 抽取表、列、类型、主外键，合并 sidecar 里的中文注释。

下游（检索、压缩、prompt）只依赖这里的 TableInfo / ColumnInfo，
不直接接触具体数据库的元数据 SQL。
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from text2sql.config import PROJECT_ROOT

_TYPE_ALIASES = {
    "int": "integer", "int4": "integer", "integer": "integer",
    "int8": "bigint", "bigint": "bigint", "long": "bigint",
    "double": "double", "float8": "double", "float": "real", "real": "real",
    "numeric": "decimal", "decimal": "decimal",
    "bool": "boolean", "boolean": "boolean",
    "timestamp": "timestamp", "datetime": "datetime",
    "varchar": "varchar", "nvarchar": "varchar", "char": "varchar", "text": "text",
    "date": "date", "blob": "blob",
}


def simplify_type(raw: str) -> str:
    """把 `decimal(18,2)` / `VARCHAR(255)` 之类的原始类型压缩成形态词。"""
    base = re.split(r"[\s(]", raw.strip().lower(), maxsplit=1)[0]
    return _TYPE_ALIASES.get(base, base)


@dataclass(frozen=True)
class ColumnInfo:
    name: str
    data_type: str
    comment: str = ""
    is_primary_key: bool = False
    foreign_key: str | None = None  # 形如 "users.user_id"

    @property
    def simple_type(self) -> str:
        return simplify_type(self.data_type)

    def render(self) -> str:
        line = f"- {self.name} ({self.simple_type})"
        if self.comment:
            line += f": {self.comment}"
        if self.is_primary_key:
            line += " (PK)"
        if self.foreign_key:
            line += f" (FK -> {self.foreign_key})"
        return line


@dataclass(frozen=True)
class TableInfo:
    db_id: str
    name: str
    comment: str
    columns: tuple[ColumnInfo, ...] = field(default_factory=tuple)

    def column(self, name: str) -> ColumnInfo | None:
        return next((c for c in self.columns if c.name == name), None)

    @property
    def primary_keys(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns if c.is_primary_key)

    @property
    def foreign_keys(self) -> tuple[ColumnInfo, ...]:
        return tuple(c for c in self.columns if c.foreign_key)

    def render(self, columns: list[str] | None = None) -> str:
        """按 PDF 的极简 DDL 格式渲染；columns 为 None 时输出全部字段。"""
        selected = self.columns if columns is None else [
            c for c in self.columns if c.name in set(columns)
        ]
        header = f"Table {self.name}"
        if self.comment:
            header += f" ({self.comment})"
        return "\n".join([header + ":"] + [c.render() for c in selected])


@dataclass(frozen=True)
class DatabaseDescriptor:
    db_id: str
    engine: str
    dialect: str
    path: Path  # 已解析为绝对路径
    schema_file: Path | None
    domain: str
    special_rules: tuple[str, ...]
    business_tables: tuple[str, ...]

    @classmethod
    def load(cls, descriptor_path: str | Path) -> "DatabaseDescriptor":
        p = Path(descriptor_path)
        raw = json.loads(p.read_text(encoding="utf-8"))
        return cls(
            db_id=raw["db_id"],
            engine=raw["engine"],
            dialect=raw.get("dialect", raw["engine"].lower()),
            path=_resolve(raw["path"]),
            schema_file=_resolve(raw["schema_file"]) if raw.get("schema_file") else None,
            domain=raw.get("domain", ""),
            special_rules=tuple(raw.get("special_rules", [])),
            business_tables=tuple(raw.get("tables", [])),
        )


@dataclass(frozen=True)
class DatabaseInfo:
    descriptor: DatabaseDescriptor
    tables: dict[str, TableInfo]

    @property
    def db_id(self) -> str:
        return self.descriptor.db_id

    @property
    def dialect(self) -> str:
        return self.descriptor.dialect

    @property
    def domain(self) -> str:
        return self.descriptor.domain

    @property
    def special_rules(self) -> tuple[str, ...]:
        return self.descriptor.special_rules

    def table(self, name: str) -> TableInfo:
        try:
            return self.tables[name]
        except KeyError:
            raise KeyError(f"{self.db_id} 中不存在表 {name}") from None

    @property
    def business_tables(self) -> dict[str, TableInfo]:
        """描述符里声明的业务表；描述符未声明时退回全部表。"""
        names = self.descriptor.business_tables or tuple(self.tables)
        return {n: self.tables[n] for n in names if n in self.tables}

    def render(self, tables: list[str] | None = None) -> str:
        names = tables if tables is not None else list(self.tables)
        return "\n\n".join(self.tables[n].render() for n in names if n in self.tables)


def _resolve(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _load_comments(schema_file: Path | None) -> dict[str, dict]:
    if not schema_file or not schema_file.exists():
        return {}
    return json.loads(schema_file.read_text(encoding="utf-8")).get("tables", {})


def load_database(descriptor_path: str | Path) -> DatabaseInfo:
    desc = DatabaseDescriptor.load(descriptor_path)
    engine = desc.engine.lower()
    if engine == "sqlite":
        raw_tables = _sqlite_tables(desc.path)
    elif engine == "duckdb":
        raw_tables = _duckdb_tables(desc.path)
    else:
        raise ValueError(f"暂不支持的数据库引擎: {desc.engine}")

    comments = _load_comments(desc.schema_file)
    tables: dict[str, TableInfo] = {}
    for name, raw in raw_tables.items():
        meta = comments.get(name, {})
        col_comments = meta.get("columns", {})
        tables[name] = TableInfo(
            db_id=desc.db_id,
            name=name,
            comment=meta.get("comment", ""),
            columns=tuple(
                ColumnInfo(
                    name=c["name"],
                    data_type=c["type"],
                    comment=col_comments.get(c["name"], ""),
                    is_primary_key=c["pk"],
                    foreign_key=c["fk"],
                )
                for c in raw["columns"]
            ),
        )
    return DatabaseInfo(descriptor=desc, tables=tables)


def _sqlite_tables(path: Path) -> dict[str, dict]:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        out = {}
        for name in names:
            cols = [
                {"name": c[1], "type": c[2], "pk": c[5] > 0, "fk": None}
                for c in con.execute(f'PRAGMA table_info("{name}")')
            ]
            fk_by_col = {}
            for fk in con.execute(f'PRAGMA foreign_key_list("{name}")'):
                # (id, seq, table, from, to, on_update, on_delete, match)
                fk_by_col[fk[3]] = f"{fk[2]}.{fk[4]}"
            for c in cols:
                c["fk"] = fk_by_col.get(c["name"])
            out[name] = {"columns": cols}
        return out
    finally:
        con.close()


def _duckdb_tables(path: Path) -> dict[str, dict]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        names = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' ORDER BY table_name").fetchall()]
        out = {}
        for name in names:
            cols = [
                {"name": r[0], "type": r[1], "pk": False, "fk": None}
                for r in con.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema='main' AND table_name=? "
                    "ORDER BY ordinal_position", [name]).fetchall()
            ]
            by_name = {c["name"]: c for c in cols}
            rows = con.execute(
                "SELECT constraint_type, constraint_column_names, "
                "referenced_table, referenced_column_names FROM duckdb_constraints() "
                "WHERE schema_name='main' AND table_name=?", [name]).fetchall()
            for ctype, col_names, ref_table, ref_cols in rows:
                col_names = list(col_names or [])
                if ctype == "PRIMARY KEY":
                    for c in col_names:
                        by_name[c]["pk"] = True
                elif ctype == "FOREIGN KEY" and ref_table:
                    for src, dst in zip(col_names, list(ref_cols or [])):
                        by_name[src]["fk"] = f"{ref_table}.{dst}"
            out[name] = {"columns": cols}
        return out
    finally:
        con.close()
