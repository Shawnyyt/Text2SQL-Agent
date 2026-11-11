"""CP7：检索层（值索引、业务知识、表/列检索、库路由、索引落盘）。"""

import json
import sqlite3
from pathlib import Path

import pytest

from text2sql.catalog import load_database
from text2sql.retrieval.business_kb import BusinessKB, split_markdown
from text2sql.retrieval.db_router_index import RouterIndex
from text2sql.retrieval.dense import DenseIndex
from text2sql.retrieval.embedder import HashingEmbedder
from text2sql.retrieval.schema_retriever import SchemaRetriever
from text2sql.retrieval.value_index import ValueIndex


@pytest.fixture
def shop_db(tmp_path: Path):
    db = tmp_path / "shop.sqlite"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE dim_region (
            province_code TEXT PRIMARY KEY,
            province_name TEXT NOT NULL,
            region_name TEXT NOT NULL
        );
        CREATE TABLE dim_city (
            city_code TEXT PRIMARY KEY,
            city_name TEXT NOT NULL,
            province_code TEXT NOT NULL REFERENCES dim_region(province_code)
        );
        CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            gender INTEGER,
            asset_amount REAL
        );
        CREATE TABLE app_event (
            event_id INTEGER PRIMARY KEY,
            event_type TEXT
        );
        INSERT INTO app_event VALUES (1, 'click'), (2, 'expose'), (3, 'click');
        CREATE TABLE sales_record (
            sale_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            city_code TEXT NOT NULL REFERENCES dim_city(city_code),
            sale_date TEXT,
            quantity INTEGER,
            sales_amount REAL
        );
        INSERT INTO dim_region VALUES
            ('11', '北京市', '华北'), ('31', '上海市', '华东'), ('32', '江苏省', '华东');
        INSERT INTO dim_city VALUES
            ('010', '北京', '11'), ('021', '上海', '31'), ('025', '南京', '32');
        INSERT INTO users VALUES (1, '张三', 1, 1200000), (2, '李四', 2, 500);
        INSERT INTO sales_record VALUES
            (1, 1, '021', '2026-10-01', 2, 199.0),
            (2, 2, '010', '2026-10-02', 1, 99.0);
    """)
    con.commit()
    con.close()
    sidecar = tmp_path / "shop.json"
    sidecar.write_text(json.dumps({"tables": {
        "dim_region": {"comment": "省份与大区维表", "columns": {
            "province_code": "省份编码", "province_name": "省份名称",
            "region_name": "所属大区"}},
        "dim_city": {"comment": "城市维表", "columns": {
            "city_code": "城市编码，如 021=上海", "city_name": "城市名称",
            "province_code": "所属省份编码"}},
        "users": {"comment": "用户主表", "columns": {
            "user_id": "用户ID", "user_name": "昵称",
            "gender": "性别编码：1=男，2=女", "asset_amount": "客户资产总额"}},
        "app_event": {"comment": "APP 埋点事件", "columns": {
            "event_id": "事件ID",
            "event_type": "事件类型：click=点击 expose=曝光"}},
        "sales_record": {"comment": "销售流水事实表", "columns": {
            "sale_id": "流水ID", "user_id": "用户ID", "city_code": "城市编码",
            "sale_date": "销售日期", "quantity": "销售件数",
            "sales_amount": "销售额（元）"}},
    }}, ensure_ascii=False), encoding="utf-8")
    desc = tmp_path / "shop_desc.json"
    desc.write_text(json.dumps({
        "db_id": "shop", "engine": "SQLite", "path": str(db),
        "schema_file": str(sidecar), "domain": "电商销售与用户运营",
        "tables": ["sales_record", "users", "dim_city", "dim_region"],
    }), encoding="utf-8")
    return load_database(desc)


def test_value_index_maps_city_name_to_code(shop_db):
    index = ValueIndex.build(shop_db)
    mapping = index.match(["上海"])[0]
    assert (mapping.table, mapping.column, mapping.value) == ("dim_city", "city_code", "021")
    assert mapping.to_prompt() == '"上海" -> dim_city.city_code = \'021\''
    assert "上海" in mapping.matched


def test_value_index_keeps_direct_equality_for_non_unique_values(shop_db):
    mapping = ValueIndex.build(shop_db).match(["华东"])[0]
    assert (mapping.table, mapping.column, mapping.value) == (
        "dim_region", "region_name", "华东")
    assert "= '华东'" in mapping.to_prompt()


def test_value_index_parses_enum_comments(shop_db):
    mapping = ValueIndex.build(shop_db).match(["男"])[0]
    assert (mapping.table, mapping.column, mapping.value) == ("users", "gender", "1")
    assert mapping.to_prompt() == '"男" -> users.gender = 1'  # 数字不加引号


def test_value_index_parses_text_enum_comments(shop_db):
    mapping = ValueIndex.build(shop_db).match(["点击"])[0]
    assert (mapping.table, mapping.column, mapping.value) == (
        "app_event", "event_type", "click")
    assert mapping.to_prompt() == "\"点击\" -> app_event.event_type = 'click'"


def test_value_index_ignores_foreign_key_code_columns(shop_db):
    # city_code 是外键编码，不能被当成"实体"映射到流水ID
    assert ValueIndex.build(shop_db).match(["021"]) == []


def test_value_index_no_match_and_roundtrip(shop_db, tmp_path):
    index = ValueIndex.build(shop_db)
    assert index.match(["火星客户"]) == []
    path = tmp_path / "values.json"
    index.save(path)
    assert ValueIndex.load(path).match(["上海"])[0].value == "021"


def test_business_kb_split_and_search(tmp_path):
    md = tmp_path / "kb.md"
    md.write_text("""# 指标字典
## 活跃用户
最近 30 天内有过登录记录且未删除的用户。
## 高净值客户
资产总额达到 100 万及以上的客户。
## 华东区
包含上海市、江苏省、浙江省。
""", encoding="utf-8")
    chunks = split_markdown(md.read_text(encoding="utf-8"))
    assert [c.title for c in chunks] == ["活跃用户", "高净值客户", "华东区"]

    emb = HashingEmbedder()
    kb = BusinessKB.build(md, emb)
    hit = kb.search("活跃用户怎么定义", emb, top_k=1)[0][0]
    assert hit.title == "活跃用户"

    kb.save(tmp_path / "kb_index")
    loaded = BusinessKB.load(tmp_path / "kb_index")
    assert loaded.search("高净值客户", emb, top_k=1)[0][0].title == "高净值客户"


def test_schema_retriever_returns_seeds_with_columns(shop_db):
    emb = HashingEmbedder()
    retriever = SchemaRetriever.build(shop_db, emb)
    result = retriever.search("销售额", emb, top_tables=4, cols_per_table=3)
    assert "sales_record" in result.seeds
    assert "sales_amount" in result.seeds["sales_record"]
    assert result.n_columns >= 1
    assert result.tables[0][0] == "sales_record"


def test_schema_retriever_respects_scope(shop_db):
    emb = HashingEmbedder()
    retriever = SchemaRetriever.build(shop_db, emb)
    scoped = retriever.search("销售额", emb, top_tables=4,
                              scope={"dim_region", "dim_city"})
    assert set(scoped.seeds) <= {"dim_region", "dim_city"}
    assert "sales_record" not in scoped.seeds


def test_schema_retriever_save_load_roundtrip(shop_db, tmp_path):
    emb = HashingEmbedder()
    retriever = SchemaRetriever.build(shop_db, emb)
    retriever.save(tmp_path / "schema_index")
    loaded = SchemaRetriever.load(tmp_path / "schema_index")
    a = retriever.search("城市订单", emb, top_tables=4)
    b = loaded.search("城市订单", emb, top_tables=4)
    assert a.seeds == b.seeds and a.tables == b.tables


def test_router_index_picks_domain(tmp_path):
    from text2sql.catalog import DatabaseDescriptor
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"db_id": "sales", "engine": "SQLite",
                             "path": "a.sqlite", "domain": "电商销售 订单 商品",
                             "tables": ["orders", "dim_product"]}), encoding="utf-8")
    b = tmp_path / "b.json"
    b.write_text(json.dumps({"db_id": "logs", "engine": "DuckDB",
                             "path": "b.duckdb", "domain": "APP 埋点日志 曝光 点击 转化率",
                             "tables": ["app_event"]}), encoding="utf-8")
    emb = HashingEmbedder()
    router = RouterIndex.build([DatabaseDescriptor.load(a),
                                DatabaseDescriptor.load(b)], emb)
    assert router.search("昨天 APP 点击转化率", emb, top_k=1)[0][0] == "logs"
    router.save(tmp_path / "router")
    assert RouterIndex.load(tmp_path / "router").db_ids == ["sales", "logs"]


def test_dense_index_subset_and_empty(tmp_path):
    emb = HashingEmbedder(dim=32)
    idx = DenseIndex.build(["a", "b", "c"], ["苹果", "香蕉", "苹果香蕉"], emb)
    assert len(idx.subset({"a", "c"}).ids) == 2
    empty = DenseIndex.build([], [], emb)
    assert empty.search(emb.encode(["x"])[0]) == []
    idx.save(tmp_path / "d", "i")
    assert DenseIndex.load(tmp_path / "d", "i").ids == ["a", "b", "c"]
