"""CP5：数据库沙盒（安全检查、只读执行、预览、报错、超时）。"""

import sqlite3
from pathlib import Path

import duckdb
import pytest

from text2sql.sandbox import Sandbox, UnsafeQueryError, check_sql


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    path = tmp_path / "shop.sqlite"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, asset REAL);
        INSERT INTO users VALUES (1, 'a', 100), (2, 'b', 200);
    """)
    con.commit()
    con.close()
    return path


@pytest.mark.parametrize("sql", [
    "DROP TABLE users",
    "DELETE FROM users",
    "UPDATE users SET asset = 0",
    "INSERT INTO users VALUES (3, 'c', 0)",
    "CREATE TABLE x (id INT)",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA table_info(users)",
    "SELECT 1; SELECT 2",
    "SELECT * INTO backup FROM users",
])
def test_unsafe_statements_are_rejected(sql, shop):
    with pytest.raises(UnsafeQueryError):
        check_sql(sql, "sqlite")


def test_allowed_queries(shop):
    r = Sandbox(shop, "sqlite").execute("SELECT name, asset FROM users ORDER BY id")
    assert r.ok, r.error
    assert r.columns == ("name", "asset")
    assert r.rows == (("a", 100.0), ("b", 200.0))
    assert r.elapsed_ms >= 0

    with_cte = Sandbox(shop, "sqlite").execute(
        "WITH t AS (SELECT * FROM users WHERE asset > 150) SELECT count(*) FROM t")
    assert with_cte.ok and with_cte.rows == ((1,),)

    union = Sandbox(shop, "sqlite").execute(
        "SELECT id FROM users WHERE id = 1 UNION ALL SELECT id FROM users WHERE id = 2")
    assert union.ok and union.rows == ((1,), (2,))


def test_database_error_is_returned_not_raised(shop):
    r = Sandbox(shop, "sqlite").execute("SELECT no_such_column FROM users")
    assert not r.ok
    assert "no such column" in r.error.lower()


def test_preview_limit_is_enforced(tmp_path: Path):
    path = tmp_path / "big.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER)")
    con.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(150)])
    con.commit()
    con.close()
    r = Sandbox(path, "sqlite", preview_limit=100).execute("SELECT * FROM t")
    assert r.ok and len(r.rows) == 100


def test_sqlite_timeout(tmp_path: Path):
    path = tmp_path / "t.sqlite"
    sqlite3.connect(path).execute("CREATE TABLE t (id INTEGER)")
    sql = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c "
           "WHERE x < 1000000000) SELECT count(*) FROM c")
    r = Sandbox(path, "sqlite", timeout=0.3).execute(sql)
    assert not r.ok and "超时" in r.error


def test_duckdb_select_and_rejection(tmp_path: Path):
    path = tmp_path / "logs.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE evt (event_type VARCHAR, n INTEGER)")
    con.execute("INSERT INTO evt VALUES ('click', 3), ('expose', 10)")
    con.close()

    box = Sandbox(path, "duckdb")
    r = box.execute("SELECT event_type, n FROM evt ORDER BY n")
    assert r.ok, r.error
    assert r.rows == (("click", 3), ("expose", 10))

    assert not box.execute("DROP TABLE evt").ok
    assert not box.execute("SELECT missing FROM evt").ok


def test_duckdb_timeout(tmp_path: Path):
    path = tmp_path / "t.duckdb"
    duckdb.connect(str(path)).close()
    sql = "SELECT count(*) FROM range(1000000000000) t(i)"
    r = Sandbox(path, "duckdb", timeout=0.3).execute(sql)
    assert not r.ok and "超时" in r.error


# ---- 函数黑名单 / 路径拦截 / 表名白名单（本轮加固）----

@pytest.mark.parametrize("sql", [
    "SELECT * FROM read_csv_auto('pyproject.toml')",
    "SELECT * FROM read_parquet('x.parquet')",
    "SELECT read_text('pyproject.toml')",
    "SELECT * FROM glob('*')",
    "SELECT http_get('http://example.com')",
])
def test_file_and_network_functions_are_denied(sql):
    with pytest.raises(UnsafeQueryError, match="禁止使用函数"):
        check_sql(sql, "duckdb")


def test_sqlite_dangerous_functions_are_denied():
    with pytest.raises(UnsafeQueryError, match="禁止使用函数"):
        check_sql("SELECT load_extension('evil')", "sqlite")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM 'C:/Windows/win.ini'",
    "SELECT * FROM 'data/sales.csv'",
    "SELECT * FROM 'logs.duckdb'",
])
def test_file_paths_as_tables_are_denied(sql):
    with pytest.raises(UnsafeQueryError, match="文件路径"):
        check_sql(sql, "duckdb")


def test_legitimate_functions_still_pass():
    check_sql("SELECT strftime('%Y-%m', t), julianday(a) - julianday(b) FROM x", "sqlite")
    check_sql("SELECT date_trunc('month', ts), CAST(x AS DOUBLE), "
              "SUM(CASE WHEN x > 0 THEN 1 ELSE 0 END) FROM y", "duckdb")


def test_allowed_tables_whitelist():
    check_sql("SELECT * FROM users", "sqlite", allowed_tables={"users"})
    with pytest.raises(UnsafeQueryError, match="未知表: nope"):
        check_sql("SELECT * FROM nope", "sqlite", allowed_tables={"users"})
    # CTE 名不是物理表，不应误报
    check_sql("WITH t AS (SELECT 1) SELECT * FROM t", "sqlite",
              allowed_tables={"users"})


def test_sandbox_reports_unknown_table_early(shop):
    box = Sandbox(shop, "sqlite", allowed_tables={"users"})
    assert box.execute("SELECT count(*) FROM users").ok
    r = box.execute("SELECT * FROM secret_table")
    assert not r.ok and "未知表" in r.error


def test_duckdb_external_access_is_disabled(tmp_path):
    path = tmp_path / "x.duckdb"
    duckdb.connect(str(path)).close()
    r = Sandbox(path, "duckdb").execute(
        "SELECT value FROM duckdb_settings() "
        "WHERE name = 'enable_external_access'")
    assert r.ok and str(r.rows[0][0]).lower() == "false"
