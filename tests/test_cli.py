"""CP12：CLI 渲染与命令入口。"""

from rich.console import Console
from typer.testing import CliRunner

from text2sql.cli import app, render_state


def _state() -> dict:
    return {
        "query": "有多少有效用户",
        "today": "2026-11-08",
        "rewritten_query": "统计有效用户数",
        "entities": ["上海"],
        "business_knowledge": [{"title": "有效订单", "text": "..."}],
        "value_mappings": [{"prompt": '"上海" -> dim_city.city_code = \'021\''}],
        "trace": [{"node": "router", "message": "路由到 sales_sqlite"},
                  {"node": "analyzer", "message": "Schema 压缩完成"},
                  {"node": "evaluator", "message": "候选优选完成"}],
        "candidates": [{"sql": "SELECT count(*) FROM users;", "ok": True,
                        "rows": [[2]], "columns": ["n"], "error": "",
                        "elapsed_ms": 3.0}],
        "attempts": [{"round": 0, "sqls": ["SELECT count(*) FROM users;"],
                      "errors": []}],
        "final_sql": "SELECT count(*) FROM users;",
        "final_candidate": {"sql": "SELECT count(*) FROM users;", "ok": True,
                            "columns": ["n"], "rows": [[2]]},
        "status": "success",
    }


def test_render_state_shows_flow_and_result():
    out = Console(record=True, width=120)
    render_state(_state(), out)
    text = out.export_text()
    assert "路由到 sales_sqlite" in text
    assert "SELECT count(*) FROM users;" in text
    assert "最终 SQL" in text
    assert "dim_city.city_code" in text
    assert "轮次：1" in text


def test_render_state_failed_shows_error():
    out = Console(record=True, width=120)
    state = _state()
    state.update(status="failed", final_sql="", error="达到最大修复轮次仍失败")
    render_state(state, out)
    text = out.export_text()
    assert "失败" in text and "达到最大修复轮次" in text


def test_cli_help_lists_commands():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("ask", "build-index", "ping"):
        assert command in result.stdout


def test_build_index_command_output(monkeypatch):
    monkeypatch.setattr(
        "text2sql.cli.build_indexes",
        lambda: {"router": {"databases": 2, "seconds": 0.1},
                 "_index_dir": {"path": "data/index"}})
    result = CliRunner().invoke(app, ["build-index"])
    assert result.exit_code == 0
    assert "databases=2" in result.stdout
    assert "data/index" in result.stdout


def test_ping_command_reports_failure(monkeypatch):
    def boom(role):
        raise RuntimeError("missing key")

    monkeypatch.setattr("text2sql.llm.ping", boom)
    result = CliRunner().invoke(app, ["ping"])
    assert result.exit_code == 0
    assert "失败" in result.stdout and "missing key" in result.stdout
