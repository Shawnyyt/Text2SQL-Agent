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
