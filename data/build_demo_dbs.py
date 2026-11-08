"""生成演示数仓（固定随机种子，可重复生成）。

产物（均被 .gitignore 忽略，随时可重建）：
    data/sales.sqlite          电商销售域（SQLite 方言）
    data/logs.duckdb           APP 埋点日志域（DuckDB 方言）
    data/schema/*.json         表 / 字段中文注释（SQLite 没有 COMMENT 语法，统一放 sidecar）

用法：python data/build_demo_dbs.py
"""

from __future__ import annotations

import csv
import json
import random
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SCHEMA = DATA / "schema"

SEED = 20260101
START = datetime(2025, 1, 1)
END = datetime(2026, 11, 7, 23, 59)
LOG_START = END - timedelta(days=60)

N_USERS = 2_000
N_PRODUCTS = 300
N_ORDERS = 20_000
N_LOGINS = 50_000
N_EVENTS = 100_000

# (省份编码, 省份, 大区)
REGIONS = [
    ("11", "北京市", "华北"),
    ("12", "天津市", "华北"),
    ("31", "上海市", "华东"),
    ("32", "江苏省", "华东"),
    ("33", "浙江省", "华东"),
    ("44", "广东省", "华南"),
    ("51", "四川省", "西南"),
    ("42", "湖北省", "华中"),
    ("61", "陕西省", "西北"),
]

# (城市编码, 城市, 省份编码) —— 注意：库里存编码，不存名字，用于实体对齐
CITIES = [
    ("010", "北京", "11"),
    ("022", "天津", "12"),
    ("021", "上海", "31"),
    ("025", "南京", "32"),
    ("0571", "杭州", "33"),
    ("020", "广州", "44"),
    ("0755", "深圳", "44"),
    ("028", "成都", "51"),
    ("027", "武汉", "42"),
    ("029", "西安", "61"),
]

CATEGORIES = ["手机数码", "家用电器", "美妆个护", "食品饮料", "运动户外", "服饰鞋包"]
BRANDS = ["星海", "云川", "北岭", "千禾", "长风", "远山"]
SEGMENTS = [
    ("SEG_HN", "高净值理财客户", "资产达到 100 万以上的客户"),
    ("SEG_NEW", "新客", "注册 30 天内的客户"),
    ("SEG_LOYAL", "忠诚客户", "近 90 天有复购的客户"),
    ("SEG_CHURN", "流失预警客户", "近 60 天无登录的客户"),
    ("SEG_STAFF", "员工客户", "内部员工账号"),
]
PAGES = [
    ("home", "首页", "推荐"),
    ("plist", "商品列表页", "导购"),
    ("pdetail", "商品详情页", "导购"),
    ("cart", "购物车页", "交易"),
    ("checkout", "结算页", "交易"),
    ("pay_ok", "支付成功页", "交易"),
    ("search", "搜索结果页", "导购"),
    ("activity", "活动落地页", "营销"),
    ("my", "我的页面", "账户"),
    ("vip", "会员中心页", "账户"),
]

# ---- 核心表：DDL + 中文注释（sidecar 用）----
CORE: dict[str, dict] = {
    "dim_region": {
        "comment": "省份与大区维表",
        "ddl": """CREATE TABLE dim_region (
            province_code TEXT PRIMARY KEY,
            province_name TEXT NOT NULL,
            region_name TEXT NOT NULL
        )""",
        "columns": {"province_code": "省份编码", "province_name": "省份名称",
                    "region_name": "所属大区，如 华东/华北/华南"},
    },
    "dim_city": {
        "comment": "城市维表，主键为电话区号形式的城市编码",
        "ddl": """CREATE TABLE dim_city (
            city_code TEXT PRIMARY KEY,
            city_name TEXT NOT NULL,
            province_code TEXT NOT NULL REFERENCES dim_region(province_code)
        )""",
        "columns": {"city_code": "城市编码，如 021=上海、010=北京",
                    "city_name": "城市名称", "province_code": "所属省份编码"},
    },
    "users": {
        "comment": "用户主表",
        "ddl": """CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT NOT NULL,
            gender INTEGER NOT NULL,
            city_code TEXT NOT NULL REFERENCES dim_city(city_code),
            register_time TEXT NOT NULL,
            asset_amount REAL NOT NULL,
            is_del INTEGER NOT NULL DEFAULT 0,
            phone TEXT
        )""",
        "columns": {"user_id": "用户ID", "user_name": "用户昵称",
                    "gender": "性别编码：1=男，2=女",
                    "city_code": "所在城市编码", "register_time": "注册时间",
                    "asset_amount": "客户在我行的资产总额（元）",
                    "is_del": "软删除标记：0=有效，1=已删除", "phone": "手机号"},
    },
    "user_login": {
        "comment": "用户登录流水",
        "ddl": """CREATE TABLE user_login (
            login_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            login_time TEXT NOT NULL,
            device_type TEXT
        )""",
        "columns": {"login_id": "登录流水ID", "user_id": "用户ID",
                    "login_time": "登录时间", "device_type": "登录设备类型"},
    },
    "dim_product": {
        "comment": "商品维表",
        "ddl": """CREATE TABLE dim_product (
            product_id INTEGER PRIMARY KEY,
            product_name TEXT NOT NULL,
            category TEXT NOT NULL,
            brand TEXT,
            unit_price REAL NOT NULL
        )""",
        "columns": {"product_id": "商品ID", "product_name": "商品名称",
                    "category": "一级品类", "brand": "品牌",
                    "unit_price": "标准单价（元）"},
    },
    "orders": {
        "comment": "订单主表",
        "ddl": """CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            product_id INTEGER NOT NULL REFERENCES dim_product(product_id),
            city_code TEXT NOT NULL REFERENCES dim_city(city_code),
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL,
            order_status INTEGER NOT NULL,
            is_del INTEGER NOT NULL DEFAULT 0,
            create_time TEXT NOT NULL
        )""",
        "columns": {"order_id": "订单ID", "user_id": "下单用户ID",
                    "product_id": "商品ID", "city_code": "下单城市编码",
                    "quantity": "购买数量", "amount": "订单实付金额（元）",
                    "order_status": "订单状态：1=待支付 2=已支付 3=已发货 4=已完成 5=已取消",
                    "is_del": "软删除标记：0=有效，1=已删除", "create_time": "下单时间"},
    },
    "sales_record": {
        "comment": "销售流水事实表（每个有效订单一条）",
        "ddl": """CREATE TABLE sales_record (
            sale_id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL REFERENCES orders(order_id),
            product_id INTEGER NOT NULL REFERENCES dim_product(product_id),
            sale_date TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            sales_amount REAL NOT NULL,
            channel TEXT NOT NULL
        )""",
        "columns": {"sale_id": "销售流水ID", "order_id": "订单ID",
                    "product_id": "商品ID", "sale_date": "销售日期（YYYY-MM-DD）",
                    "quantity": "销售件数", "sales_amount": "销售额（元）",
                    "channel": "销售渠道：app/h5/pc"},
    },
    "customer_segment": {
        "comment": "客户分群定义表，只能经 customer_tag 关联到用户",
        "ddl": """CREATE TABLE customer_segment (
            segment_id TEXT PRIMARY KEY,
            segment_name TEXT NOT NULL,
            description TEXT
        )""",
        "columns": {"segment_id": "分群编码", "segment_name": "分群名称",
                    "description": "分群定义说明"},
    },
    "customer_tag": {
        "comment": "客户标签表，用于桥接用户与客户分群",
        "ddl": """CREATE TABLE customer_tag (
            tag_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(user_id),
            segment_id TEXT NOT NULL REFERENCES customer_segment(segment_id),
            tag_name TEXT NOT NULL,
            tag_value TEXT,
            update_time TEXT NOT NULL
        )""",
        "columns": {"tag_id": "标签ID", "user_id": "用户ID",
                    "segment_id": "所属客户分群编码", "tag_name": "标签名",
                    "tag_value": "标签值", "update_time": "标签更新时间"},
    },
}

# ---- 噪声表：模拟数仓里的历史/财务/风控等无关明细，用于体现检索与压缩收益 ----
NOISE = [
    "ods_user_action_log_hist", "ods_device_info_snapshot", "ods_coupon_issue_log",
    "dwd_trade_pay_flow", "dwd_trade_refund_detail", "dwd_marketing_activity_stat",
    "dws_user_growth_daily", "dws_product_sales_daily", "ads_region_kpi_monthly",
    "ads_channel_roi_daily", "dim_shop", "dim_supplier", "dim_warehouse", "dim_staff",
    "dim_campaign", "fin_receivable_detail", "fin_invoice_record", "risk_blacklist",
    "crm_lead_opportunity", "ops_stock_snapshot",
]

LOG_TABLES: dict[str, dict] = {
    "app_event": {
        "comment": "APP 埋点事件明细（曝光 / 点击 / 下单）",
        "ddl": """CREATE TABLE app_event (
            event_id BIGINT PRIMARY KEY,
            user_id BIGINT,
            page_id VARCHAR,
            event_type VARCHAR,
            event_time TIMESTAMP,
            dt DATE,
            device VARCHAR
        )""",
        "columns": {"event_id": "事件ID", "user_id": "用户ID",
                    "page_id": "页面编码", "event_type": "事件类型：expose=曝光 click=点击 order=下单",
                    "event_time": "事件时间", "dt": "事件日期（分区字段）",
                    "device": "设备类型：ios/android"},
    },
    "dim_page": {
        "comment": "页面维表",
        "ddl": """CREATE TABLE dim_page (
            page_id VARCHAR PRIMARY KEY,
            page_name VARCHAR,
            module VARCHAR
        )""",
        "columns": {"page_id": "页面编码", "page_name": "页面名称",
                    "module": "所属模块：推荐/导购/交易/营销/账户"},
    },
}


def _rand_time(rng: random.Random, start: datetime, end: datetime) -> datetime:
    return start + timedelta(seconds=rng.randrange(int((end - start).total_seconds())))


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def build_sales(path: Path) -> tuple[dict[str, int], dict[str, dict]]:
    rng = random.Random(SEED)
    meta = {name: spec for name, spec in CORE.items()}
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    cur = con.cursor()

    for spec in CORE.values():
        cur.execute(spec["ddl"])
    for name in NOISE:
        n_cols = rng.randint(30, 60)
        cols = ", ".join(f"f_{i:02d} TEXT" for i in range(1, n_cols + 1))
        cur.execute(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY, {cols}, created_at TEXT)")
        meta[name] = {
            "comment": f"历史明细表 {name}（与本业务问题无关）",
            "columns": {f"f_{i:02d}": f"历史明细字段 f_{i:02d}" for i in range(1, n_cols + 1)},
        }

    cur.executemany("INSERT INTO dim_region VALUES (?,?,?)", REGIONS)
    cur.executemany("INSERT INTO dim_city VALUES (?,?,?)", CITIES)

    users = []
    for uid in range(1, N_USERS + 1):
        city = rng.choice(CITIES)[0]
        if rng.random() < 0.06:
            asset = round(rng.uniform(1_000_000, 8_000_000), 2)
        else:
            asset = round(min(900_000.0, rng.lognormvariate(11.5, 1.1)), 2)
        users.append((
            uid, f"用户{uid:04d}", rng.choice([1, 2]), city,
            _fmt(_rand_time(rng, START, END - timedelta(days=90))),
            asset, 1 if rng.random() < 0.05 else 0,
            f"138{rng.randrange(10**8):08d}",
        ))
    cur.executemany("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", users)
    user_city = {u[0]: u[3] for u in users}

    products = []
    for pid in range(1, N_PRODUCTS + 1):
        products.append((
            pid, f"{rng.choice(BRANDS)}{rng.choice(CATEGORIES)}{pid:03d}",
            rng.choice(CATEGORIES), rng.choice(BRANDS),
            round(rng.uniform(19, 5999), 2),
        ))
    cur.executemany("INSERT INTO dim_product VALUES (?,?,?,?,?)", products)
    price = {p[0]: p[4] for p in products}

    orders, sales = [], []
    for oid in range(1, N_ORDERS + 1):
        uid = rng.randrange(1, N_USERS + 1)
        pid = rng.randrange(1, N_PRODUCTS + 1)
        qty = rng.choices([1, 2, 3, 4], weights=[70, 20, 7, 3])[0]
        amount = round(price[pid] * qty * rng.uniform(0.75, 1.0), 2)
        status = rng.choices([1, 2, 3, 4, 5], weights=[5, 10, 10, 70, 5])[0]
        is_del = 1 if rng.random() < 0.03 else 0
        created = _rand_time(rng, START, END)
        city = user_city[uid] if rng.random() < 0.9 else rng.choice(CITIES)[0]
        orders.append((oid, uid, pid, city, qty, amount, status, is_del, _fmt(created)))
        # 取消 / 删除的订单不进销售流水
        if status != 5 and not is_del:
            sales.append((len(sales) + 1, oid, pid, created.strftime("%Y-%m-%d"),
                          qty, amount, rng.choice(["app", "h5", "pc"])))
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?)", orders)
    cur.executemany("INSERT INTO sales_record VALUES (?,?,?,?,?,?,?)", sales)

    logins = [
        (i + 1, rng.randrange(1, N_USERS + 1), _fmt(_rand_time(rng, START, END)),
         rng.choice(["ios", "android", "web"]))
        for i in range(N_LOGINS)
    ]
    cur.executemany("INSERT INTO user_login VALUES (?,?,?,?)", logins)

    cur.executemany("INSERT INTO customer_segment VALUES (?,?,?)", SEGMENTS)
    tags, tid = [], 0
    for uid in range(1, N_USERS + 1):
        if rng.random() > 0.3:
            continue
        segs = [s[0] for s in SEGMENTS if rng.random() < 0.25] or ["SEG_NEW"]
        for seg in segs:
            tid += 1
            tags.append((tid, uid, seg, "风险偏好", rng.choice(["稳健", "平衡", "进取"]),
                         _fmt(_rand_time(rng, START, END))))
    cur.executemany("INSERT INTO customer_tag VALUES (?,?,?,?,?,?)", tags)

    con.commit()
    con.close()
    counts = {"users": N_USERS, "orders": N_ORDERS, "sales_record": len(sales),
              "user_login": N_LOGINS, "customer_tag": len(tags)}
    return counts, meta


def build_logs(path: Path) -> dict[str, int]:
    rng = random.Random(SEED + 1)
    if path.exists():
        path.unlink()
    con = duckdb.connect(str(path))
    for spec in LOG_TABLES.values():
        con.execute(spec["ddl"])
    con.executemany("INSERT INTO dim_page VALUES (?,?,?)", PAGES)

    page_ids = [p[0] for p in PAGES]
    # DuckDB 的 executemany 逐行插入很慢（10 万行约 5 分钟），改为先写 CSV 再 COPY 导入
    tmp = path.with_name(path.stem + "_events.csv")
    with tmp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for eid in range(1, N_EVENTS + 1):
            ts = _rand_time(rng, LOG_START, END)
            writer.writerow([
                eid, rng.randrange(1, N_USERS + 1), rng.choice(page_ids),
                rng.choices(["expose", "click", "order"], weights=[70, 25, 5])[0],
                ts.strftime("%Y-%m-%d %H:%M:%S"), ts.strftime("%Y-%m-%d"),
                rng.choice(["ios", "android"]),
            ])
    con.execute("COPY app_event FROM ? (FORMAT CSV, HEADER false)", [tmp.as_posix()])
    tmp.unlink()
    con.close()
    return {"app_event": N_EVENTS, "dim_page": len(PAGES)}


def write_schema_sidecar(db_id: str, tables: dict[str, dict]) -> Path:
    SCHEMA.mkdir(parents=True, exist_ok=True)
    out = SCHEMA / f"{db_id}.json"
    payload = {
        "tables": {
            name: {"comment": spec["comment"], "columns": spec["columns"]}
            for name, spec in tables.items()
        }
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    DATA.mkdir(exist_ok=True)
    counts_sales, meta_sales = build_sales(DATA / "sales.sqlite")
    counts_logs = build_logs(DATA / "logs.duckdb")
    s1 = write_schema_sidecar("sales_sqlite", meta_sales)
    s2 = write_schema_sidecar("logs_duckdb", LOG_TABLES)
    print("sales.sqlite :", counts_sales)
    print("logs.duckdb  :", counts_logs)
    print("注释 sidecar :", s1.relative_to(ROOT), s2.relative_to(ROOT))


if __name__ == "__main__":
    main()
