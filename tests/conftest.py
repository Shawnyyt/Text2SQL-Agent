"""共享测试夹具：一个小型双库环境（SQLite 销售库 + DuckDB 埋点库）。"""

import json
import sqlite3
from pathlib import Path

import duckdb
import pytest

from text2sql.catalog import load_database
from text2sql.config import Settings
from text2sql.retrieval.business_kb import BusinessKB
from text2sql.retrieval.db_router_index import RouterIndex
from text2sql.retrieval.embedder import HashingEmbedder
from text2sql.retrieval.schema_retriever import SchemaRetriever
from text2sql.retrieval.value_index import ValueIndex


def _write_sidecar(path: Path, tables: dict) -> None:
    path.write_text(json.dumps({"tables": tables}, ensure_ascii=False),
                    encoding="utf-8")


@pytest.fixture
def parts(tmp_path: Path):
    # ---- 销售库 ----
    shop = tmp_path / "shop.sqlite"
    con = sqlite3.connect(shop)
    con.executescript("""
        CREATE TABLE dim_region (province_code TEXT PRIMARY KEY,
                                 province_name TEXT, region_name TEXT);
        CREATE TABLE dim_city (city_code TEXT PRIMARY KEY, city_name TEXT,
                               province_code TEXT REFERENCES dim_region(province_code));
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, gender INTEGER,
                            asset_amount REAL, is_del INTEGER);
        CREATE TABLE customer_segment (segment_id TEXT PRIMARY KEY, segment_name TEXT);
        CREATE TABLE customer_tag (tag_id INTEGER PRIMARY KEY,
                                   user_id INTEGER REFERENCES users(user_id),
                                   segment_id TEXT REFERENCES customer_segment(segment_id));
        CREATE TABLE sales_record (sale_id INTEGER PRIMARY KEY,
                                   user_id INTEGER REFERENCES users(user_id),
                                   city_code TEXT REFERENCES dim_city(city_code),
                                   sale_date TEXT, quantity INTEGER, sales_amount REAL);
        INSERT INTO dim_region VALUES ('31','上海市','华东'),('11','北京市','华北');
        INSERT INTO dim_city VALUES ('021','上海','31'),('010','北京','11');
        INSERT INTO users VALUES (1,1,1200000,0),(2,2,500,0);
        INSERT INTO customer_segment VALUES ('SEG_HN','高净值理财客户');
        INSERT INTO customer_tag VALUES (1,1,'SEG_HN');
        INSERT INTO sales_record VALUES (1,1,'021','2026-10-01',2,199.0);
    """)
    con.commit()
    con.close()
    _write_sidecar(tmp_path / "shop.json", {
        "dim_region": {"comment": "省份与大区维表", "columns": {
            "province_code": "省份编码", "province_name": "省份名称", "region_name": "所属大区"}},
        "dim_city": {"comment": "城市维表", "columns": {
            "city_code": "城市编码，如 021=上海", "city_name": "城市名称",
            "province_code": "所属省份编码"}},
        "users": {"comment": "用户主表", "columns": {
            "user_id": "用户ID", "gender": "性别编码：1=男，2=女",
            "asset_amount": "客户资产总额", "is_del": "软删除标记"}},
        "customer_segment": {"comment": "客户分群定义表", "columns": {
            "segment_id": "分群编码", "segment_name": "分群名称"}},
        "customer_tag": {"comment": "客户标签桥接表", "columns": {
            "tag_id": "标签ID", "user_id": "用户ID", "segment_id": "分群编码"}},
        "sales_record": {"comment": "销售流水事实表", "columns": {
            "sale_id": "流水ID", "user_id": "用户ID", "city_code": "城市编码",
            "sale_date": "销售日期", "quantity": "销售件数", "sales_amount": "销售额"}},
    })
    shop_desc = tmp_path / "shop_desc.json"
    shop_desc.write_text(json.dumps({
        "db_id": "shop", "engine": "SQLite", "path": str(shop),
        "schema_file": str(tmp_path / "shop.json"),
        "domain": "电商销售 订单 商品 用户 客户分群",
        "tables": ["sales_record", "users", "customer_segment", "customer_tag",
                   "dim_city", "dim_region"],
    }, ensure_ascii=False), encoding="utf-8")

    # ---- 埋点库 ----
    logs = tmp_path / "logs.duckdb"
    con = duckdb.connect(str(logs))
    con.execute("CREATE TABLE app_event (event_id BIGINT PRIMARY KEY, "
                "event_type VARCHAR, dt DATE)")
    con.executemany("INSERT INTO app_event VALUES (?,?,?)",
                    [(1, "expose", "2026-11-10"), (2, "click", "2026-11-10")])
    con.close()
    _write_sidecar(tmp_path / "logs.json", {
        "app_event": {"comment": "APP 埋点事件", "columns": {
            "event_id": "事件ID", "event_type": "事件类型：click=点击 expose=曝光",
            "dt": "事件日期"}},
    })
    logs_desc = tmp_path / "logs_desc.json"
    logs_desc.write_text(json.dumps({
        "db_id": "logs", "engine": "DuckDB", "path": str(logs),
        "schema_file": str(tmp_path / "logs.json"),
        "domain": "APP 埋点 日志 曝光 点击 转化率",
        "tables": ["app_event"],
    }, ensure_ascii=False), encoding="utf-8")

    kb_path = tmp_path / "kb.md"
    kb_path.write_text("""# 指标字典
## 高净值客户
资产总额达到 100 万及以上的客户。
## 活跃用户
最近 30 天内有过登录记录的用户。
""", encoding="utf-8")

    databases = {db.db_id: db for db in (
        load_database(shop_desc), load_database(logs_desc))}
    settings = Settings(_env_file=None, data_dir=tmp_path,
                        retrieval_top_tables=3, retrieval_cols_per_table=2,
                        retrieval_business_kb_top_k=1)
    embedder = HashingEmbedder()
    descriptors = [db.descriptor for db in databases.values()]
    dialect_dir = tmp_path / "dialects"
    dialect_dir.mkdir()
    (dialect_dir / "sqlite.md").write_text("# SQLite 方言\n- 日期用 strftime",
                                           encoding="utf-8")
    (dialect_dir / "duckdb.md").write_text("# DuckDB 方言\n- 比率需 CAST AS DOUBLE",
                                           encoding="utf-8")
    return {
        "settings": settings, "databases": databases, "embedder": embedder,
        "router": RouterIndex.build(descriptors, embedder),
        "descriptors": {d.db_id: d for d in descriptors},
        "business_kb": BusinessKB.build(kb_path, embedder),
        "retrievers": {k: SchemaRetriever.build(v, embedder) for k, v in databases.items()},
        "value_indexes": {k: ValueIndex.build(v) for k, v in databases.items()},
        "dialect_dir": dialect_dir,
    }

