"""CP4：Schema 压缩（主键补全、桥接表补全、渲染与 token 估算）。"""

import json
import sqlite3
from pathlib import Path

import pytest

from text2sql.catalog import load_database
from text2sql.compression import (
    build_fk_graph,
    compress_schema,
    estimate_tokens,
    shortest_path,
)


@pytest.fixture
def db(tmp_path: Path):
    """users ← customer_tag → customer_segment，外加一张无关表。"""
    path = tmp_path / "shop.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            asset_amount REAL,
            is_del INTEGER
        );
        CREATE TABLE customer_segment (
            segment_id TEXT PRIMARY KEY,
            segment_name TEXT
        );
        CREATE TABLE customer_tag (
            tag_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            segment_id TEXT NOT NULL REFERENCES customer_segment(segment_id),
            tag_name TEXT
        );
        CREATE TABLE noise (
            id INTEGER PRIMARY KEY,
            f1 TEXT, f2 TEXT, f3 TEXT
        );
    """)
    con.commit()
    con.close()
    desc = tmp_path / "d.json"
    desc.write_text(json.dumps({"db_id": "shop", "engine": "SQLite",
                                "path": str(path)}), encoding="utf-8")
    return load_database(desc)


def test_seed_columns_and_primary_key_completion(db):
    r = compress_schema(db, {"users": ["asset_amount"]})
    assert r.tables == {"users": ("user_id", "asset_amount")}
    assert "users (asset_amount)" not in r.schema_text
    assert "- user_id (integer) (PK)" in r.schema_text
    assert "user_name" not in r.schema_text


def test_bridge_table_is_added_with_join_keys(db):
    r = compress_schema(db, {"users": ["asset_amount"],
                             "customer_segment": ["segment_name"]})
    assert set(r.tables) == {"users", "customer_segment", "customer_tag"}
    assert {"user_id", "segment_id"} <= set(r.tables["customer_tag"])
    assert "customer_tag" in r.schema_text
    assert "(FK -> users.user_id)" in r.schema_text


def test_fk_between_selected_tables_keeps_both_sides(db):
    r = compress_schema(db, {"customer_tag": ["tag_name"], "users": ["user_name"]})
    assert "user_id" in r.tables["customer_tag"]
    assert "user_id" in r.tables["users"]


def test_bridges_can_be_disabled(db):
    r = compress_schema(db, {"users": ["asset_amount"],
                             "customer_segment": ["segment_name"]},
                        add_bridges=False)
    assert "customer_tag" not in r.tables


def test_columns_are_in_table_order_and_deduplicated(db):
    r = compress_schema(db, {"users": ["is_del", "asset_amount", "is_del"]})
    assert r.tables["users"] == ("user_id", "asset_amount", "is_del")


def test_unknown_table_or_column_raises(db):
    with pytest.raises(KeyError, match="nope"):
        compress_schema(db, {"nope": ["x"]})
    with pytest.raises(KeyError, match="asset"):
        compress_schema(db, {"users": ["asset"]})


def test_seed_none_keeps_all_columns(db):
    r = compress_schema(db, {"customer_segment": None})
    assert r.tables["customer_segment"] == ("segment_id", "segment_name")


def test_token_estimate_and_compression_ratio(db):
    r = compress_schema(db, {"users": ["asset_amount", "is_del"]})
    assert r.compressed_tokens < r.full_tokens
    assert 0 < r.ratio < 1
    assert "压缩" in r.summary
    assert estimate_tokens("") == 1
    assert estimate_tokens("SELECT * FROM t") >= 3


def test_fk_graph_is_undirected_and_path_is_shortest(db):
    adj = build_fk_graph(db)
    assert ("customer_tag", "user_id", "users", "user_id") in adj["customer_tag"]
    assert ("users", "user_id", "customer_tag", "user_id") in adj["users"]
    path = shortest_path(adj, "users", "customer_segment")
    assert path == [
        ("users", "user_id", "customer_tag", "user_id"),
        ("customer_tag", "segment_id", "customer_segment", "segment_id"),
    ]
    assert shortest_path(adj, "users", "noise") is None
    assert shortest_path(adj, "users", "users") == []
