"""数据库沙盒：只读执行 + AST 安全检查 + 超时。

职责（对应 PDF 「数据库沙盒 Sandbox Executor」）：
    1. 用 sqlglot 解析，只允许单条 SELECT / WITH 查询，拦截 DML/DDL/ATTACH/PRAGMA 等
    2. 用只读连接执行：SQLite 以 mode=ro URI 打开，DuckDB 以 read_only=True 打开
    3. 先 EXPLAIN 验证，再用外层 LIMIT 真实执行一小段，拿到报错或结果预览
    4. 超时即中断，避免复杂查询把进程卡死

Executor 节点只依赖 QueryResult：ok=False 时 error 就是喂给自我修正的报错信息。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import sqlglot
from sqlglot import exp


class UnsafeQueryError(ValueError):
    """SQL 未通过安全检查。"""


@dataclass(frozen=True)
class QueryResult:
    sql: str
    ok: bool
    columns: tuple[str, ...] = ()
    rows: tuple[tuple, ...] = field(default_factory=tuple)
    error: str = ""
    elapsed_ms: float = 0.0


def check_sql(sql: str, dialect: str) -> str:
    """AST 安全检查，通过则返回去掉首尾空白的 SQL，否则抛 UnsafeQueryError。"""
    text = sql.strip()
    if not text:
        raise UnsafeQueryError("SQL 为空")
    try:
        statements = [s for s in sqlglot.parse(text, read=dialect) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise UnsafeQueryError(f"SQL 解析失败: {exc}") from None
    if len(statements) != 1:
        raise UnsafeQueryError(f"只允许单条语句，实际解析出 {len(statements)} 条")
    root = statements[0]
    if not isinstance(root, exp.Query):
        raise UnsafeQueryError(f"只允许 SELECT 查询，不允许 {root.key.upper()} 语句")
    if root.find(exp.Into):
        raise UnsafeQueryError("不允许 SELECT ... INTO")
    return text


class Sandbox:
    """绑定一个只读数据库的查询沙盒。"""

    def __init__(self, db_path: str | Path, dialect: str, *,
                 timeout: float = 10.0, preview_limit: int = 100):
        self.path = Path(db_path)
        self.dialect = dialect
        self.timeout = timeout
        self.preview_limit = preview_limit

    def __enter__(self) -> "Sandbox":
        return self

    def __exit__(self, *exc) -> None:
        pass

    def execute(self, sql: str) -> QueryResult:
        start = time.monotonic()
        try:
            safe_sql = check_sql(sql, self.dialect)
        except UnsafeQueryError as exc:
            return QueryResult(sql=sql, ok=False, error=str(exc),
                               elapsed_ms=_ms(start))
        try:
            columns, rows = self._run(safe_sql)
        except _QueryTimeout:
            return QueryResult(sql=safe_sql, ok=False,
                               error=f"查询超时（>{self.timeout:g}s）",
                               elapsed_ms=_ms(start))
        except Exception as exc:  # 驱动级报错原样交给自我修正
            return QueryResult(sql=safe_sql, ok=False,
                               error=f"{type(exc).__name__}: {exc}",
                               elapsed_ms=_ms(start))
        return QueryResult(sql=safe_sql, ok=True, columns=tuple(columns),
                           rows=tuple(rows), elapsed_ms=_ms(start))

    def _run(self, sql: str) -> tuple[list[str], list[tuple]]:
        engine = self.dialect.lower()
        if engine == "sqlite":
            return self._run_sqlite(sql)
        if engine == "duckdb":
            return self._run_duckdb(sql)
        raise ValueError(f"沙盒暂不支持方言: {self.dialect}")

    def _preview_sql(self, sql: str) -> str:
        inner = sql.rstrip().rstrip(";")
        return f"SELECT * FROM (\n{inner}\n) AS _preview LIMIT {self.preview_limit}"

    def _run_sqlite(self, sql: str) -> tuple[list[str], list[tuple]]:
        con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True,
                              timeout=5)
        deadline = time.monotonic() + self.timeout

        def progress() -> int:
            return 1 if time.monotonic() > deadline else 0

        try:
            con.execute("PRAGMA query_only = ON")
            # 1) EXPLAIN 验证语法与对象是否存在（不真正取数）
            con.execute(f"EXPLAIN {sql}")
            # 2) 外层 LIMIT 真实执行，拿到结果预览
            con.set_progress_handler(progress, 10_000)
            cur = con.execute(self._preview_sql(sql))
            rows = [tuple(r) for r in cur.fetchall()]
            columns = [d[0] for d in cur.description or ()]
            return columns, rows
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise _QueryTimeout from None
            raise
        finally:
            con.close()

    def _run_duckdb(self, sql: str) -> tuple[list[str], list[tuple]]:
        con = duckdb.connect(str(self.path), read_only=True)
        done = threading.Event()

        def interrupt() -> None:
            if not done.is_set():
                con.interrupt()

        timer = threading.Timer(self.timeout, interrupt)
        timer.daemon = True
        timer.start()
        try:
            con.execute(f"EXPLAIN {sql}")
            cur = con.execute(self._preview_sql(sql))
            rows = [tuple(r) for r in cur.fetchmany(self.preview_limit)]
            columns = [d[0] for d in cur.description or ()]
            return columns, rows
        except duckdb.InterruptException:
            raise _QueryTimeout from None
        finally:
            done.set()
            timer.cancel()
            con.close()


class _QueryTimeout(Exception):
    pass


def _ms(start: float) -> float:
    return round((time.monotonic() - start) * 1000, 1)
