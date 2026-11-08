"""CP3：元数据目录（SQLite / DuckDB 抽取 + 注释合并 + 渲染）。"""

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from text2sql.catalog import (
    ColumnInfo,
    DatabaseDescriptor,
    load_database,
    simplify_type,
)


@pytest.fixture
def sqlite_desc(tmp_path: Path) -> Path:
    db = tmp_path / "shop.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            user_name VARCHAR(64) NOT NULL
        );
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            amount DECIMAL(18,2),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );
    """)
    con.commit()
    con.close()
    sidecar = tmp_path / "shop.json"
    sidecar.write_text(json.dumps({"tables": {
        "users": {"comment": "用户主表",
                  "columns": {"user_id": "用户ID", "user_name": "昵称"}},
        "orders": {"comment": "订单主表",
                   "columns": {"order_id": "订单ID", "user_id": "用户ID",
                               "amount": "金额"}},
    }}, ensure_ascii=False), encoding="utf-8")
    desc = tmp_path / "desc.json"
    desc.write_text(json.dumps({
        "db_id": "shop", "engine": "SQLite", "path": str(db),
        "schema_file": str(sidecar), "domain": "电商",
        "special_rules": ["分页用 LIMIT"],
        "tables": ["orders", "users"],
    }), encoding="utf-8")
    return desc


def test_sqlite_introspection(sqlite_desc: Path):
    info = load_database(sqlite_desc)
    assert set(info.tables) == {"users", "orders"}
    orders = info.table("orders")
    assert orders.comment == "订单主表"
    assert orders.primary_keys == ("order_id",)
    fk = orders.column("user_id")
    assert fk.foreign_key == "users.user_id"
    assert fk.comment == "用户ID"
    assert orders.column("amount").simple_type == "decimal"
    assert info.dialect == "sqlite"
    assert info.business_tables == {"orders": orders, "users": info.table("users")}


def test_render_matches_pdf_format(sqlite_desc: Path):
    text = load_database(sqlite_desc).table("orders").render()
    assert text.splitlines() == [
        "Table orders (订单主表):",
        "- order_id (integer): 订单ID (PK)",
        "- user_id (integer): 用户ID (FK -> users.user_id)",
        "- amount (decimal): 金额",
    ]
    # 只渲染选中的列
    picked = load_database(sqlite_desc).table("orders").render(["amount"])
    assert "user_id" not in picked and "amount" in picked


def test_descriptor_requires_absolute_or_resolves_relative(tmp_path: Path):
    p = tmp_path / "d.json"
    p.write_text(json.dumps({"db_id": "x", "engine": "SQLite", "path": "data/x.sqlite"}))
    d = DatabaseDescriptor.load(p)
    assert d.path.is_absolute() and d.path.name == "x.sqlite"


def test_duckdb_introspection(tmp_path: Path):
    db = tmp_path / "logs.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE page (id VARCHAR PRIMARY KEY, name VARCHAR)")
    con.execute("CREATE TABLE evt (id BIGINT PRIMARY KEY, page_id VARCHAR REFERENCES page(id), ts TIMESTAMP)")
    con.close()
    desc = tmp_path / "desc.json"
    desc.write_text(json.dumps({
        "db_id": "logs", "engine": "DuckDB", "path": str(db), "tables": ["evt"],
    }), encoding="utf-8")
    info = load_database(desc)
    evt = info.table("evt")
    assert evt.primary_keys == ("id",)
    assert evt.column("page_id").foreign_key == "page.id"
    assert evt.column("ts").simple_type == "timestamp"


def test_type_simplification():
    assert simplify_type("VARCHAR(255)") == "varchar"
    assert simplify_type("decimal(18, 2)") == "decimal"
    assert simplify_type("timestamp without time zone") == "timestamp"
    assert simplify_type("INT8") == "bigint"


def test_unknown_engine(tmp_path: Path):
    desc = tmp_path / "d.json"
    desc.write_text(json.dumps({"db_id": "x", "engine": "Oracle", "path": "x.dbf"}))
    with pytest.raises(ValueError, match="Oracle"):
        load_database(desc)


def test_column_render_flags():
    c = ColumnInfo("id", "BIGINT", "主键", is_primary_key=True)
    assert c.render() == "- id (bigint): 主键 (PK)"
