# 企业级多智能体 Text2SQL

基于 LangGraph 双 Agent 协同的自然语言查询引擎：语义路由 → 多粒度检索（库/表/列/值）→ AST 智能 Schema 压缩 → 多候选 SQL 生成 → 只读沙盒执行 → 报错驱动的自我修正 → 结果投票/LLM 判优。

当前处于逐步实现阶段，已完成元数据目录、Schema 压缩、数据库沙盒、检索基础件（见 [HANDOFF.md](HANDOFF.md) 的进度表）。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python data/build_demo_dbs.py
.venv/Scripts/python -m pytest -q
```

Linux / macOS 使用 `.venv/bin/python`。

## 文档

- [HANDOFF.md](HANDOFF.md)：交接文档（现状、模块职责、接口契约、设计决策、已知限制、下一步）
- `data/knowledge/business_glossary.md`：业务口径与指标字典
- `data/dialects/`：SQLite / DuckDB 方言补丁

## 说明

- 演示数据由 `data/build_demo_dbs.py` 以固定随机种子生成，可随时重建，不纳入版本库。
- 沙盒是"逻辑沙盒"（只读连接 + AST 白名单 + 资源限制），不是容器隔离；生产部署注意事项见交接文档第 9 节。
