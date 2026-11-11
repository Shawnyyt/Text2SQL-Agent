"""命令行入口：text2sql ask / build-index / ping。"""

from __future__ import annotations

import json
import sys
from datetime import date

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from text2sql.graph import build_graph
from text2sql.indexing import build_indexes
from text2sql.runtime import Runtime
from text2sql.state import Text2SQLState, initial_state

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(add_completion=False,
                  help="基于多智能体协同的企业级 Text2SQL")
console = Console()


def render_state(state: Text2SQLState, out: Console | None = None) -> None:
    """把一次完整运行按阶段打印出来。"""
    out = out or console
    out.rule("处理流程")
    for item in state.get("trace", []):
        out.print(f"[bold cyan]{item['node']:<9}[/bold cyan] {item['message']}")

    if state.get("rewritten_query"):
        out.print(f"\n改写：{state['rewritten_query']}")
    if state.get("entities"):
        out.print(f"实体：{state['entities']}")
    if state.get("business_knowledge"):
        out.print("业务口径：" + "、".join(k["title"] for k in state["business_knowledge"]))
    if state.get("value_mappings"):
        out.print("值映射：" + "；".join(m["prompt"] for m in state["value_mappings"]))

    candidates = state.get("candidates", [])
    if candidates:
        table = Table(title="候选 SQL", show_lines=False)
        for column in ("#", "ok", "ms", "SQL", "错误"):
            table.add_column(column, overflow="fold")
        for index, candidate in enumerate(candidates, 1):
            table.add_row(str(index), "✓" if candidate.get("ok") else "✗",
                          str(candidate.get("elapsed_ms", "")),
                          (candidate.get("sql") or "").replace("\n", " ")[:120],
                          (candidate.get("error") or "")[:80])
        out.print(table)

    if state.get("attempts"):
        out.print(f"轮次：{len(state['attempts'])}（首次 + "
                  f"{len(state['attempts']) - 1} 次修复）")

    if state.get("final_sql"):
        out.print(Panel(state["final_sql"], title="最终 SQL", border_style="green"))
    final = state.get("final_candidate") or {}
    if final.get("rows"):
        table = Table(title=f"结果（前 {len(final['rows'])} 行）")
        for name in final.get("columns", []):
            table.add_column(str(name))
        for row in final["rows"][:20]:
            table.add_row(*[str(v) for v in row])
        out.print(table)

    if state.get("status") == "failed":
        out.print(Panel(state.get("error", "执行失败"), title="失败", border_style="red"))


def _run(query: str, today: date | None, runtime: Runtime) -> Text2SQLState:
    return build_graph(runtime).invoke(initial_state(query, today))


@app.command()
def ask(
    query: str = typer.Argument(..., help="自然语言问题"),
    today: str | None = typer.Option(None, help="指定“今天”（YYYY-MM-DD），便于复现"),
    json_output: bool = typer.Option(False, "--json", help="输出完整 state 的 JSON"),
) -> None:
    """回答一个自然语言问题。"""
    try:
        runtime = Runtime.from_settings()
    except Exception as exc:
        console.print(f"[red]初始化失败[/red]：{exc}")
        raise typer.Exit(code=1) from None
    day = date.fromisoformat(today) if today else None
    state = _run(query, day, runtime)
    if json_output:
        payload = {k: v for k, v in state.items()}
        console.print_json(json.dumps(payload, ensure_ascii=False, default=str))
        return
    render_state(state)


@app.command("build-index")
def build_index_command() -> None:
    """重建检索索引（data/index/）。"""
    stats = build_indexes()
    for name, info in stats.items():
        if name.startswith("_"):
            continue
        console.print(f"[bold]{name}[/bold] " + " ".join(f"{k}={v}" for k, v in info.items()))
    console.print(f"索引已写入 {stats['_index_dir']['path']}")


@app.command()
def ping() -> None:
    """检查 Analyzer / Coder 两个 LLM 接口是否连通。"""
    from text2sql.llm import ping as ping_llm

    for role in ("analyzer", "coder"):
        try:
            reply = ping_llm(role)
            console.print(f"[green]{role}[/green] OK: {reply[:40]!r}")
        except Exception as exc:  # 直接展示服务商返回的错误
            console.print(f"[red]{role}[/red] 失败: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    app()
