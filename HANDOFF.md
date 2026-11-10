# 交接文档

> 最后更新：2026-11-10 · 对应提交 `4ffc3ab`（检查点 6）
> 源方案：`企业级多智能体 text2sql 项目.pdf`（本地文件，未纳入仓库）

## 1. 项目是什么

按 PDF《企业级多智能体 text2sql 项目》落地的可运行系统：**Router（语义路由 + 方言补丁）→ Analyzer（多粒度检索 + Schema 压缩 + 执行计划）→ Coder（多候选 SQL + 自我修正）→ 沙盒执行 → Evaluator（结果投票 / LLM 判优）**。

目标不是复刻某个开源项目，而是把 PDF 里描述的能力逐一实现、可运行、可验证：

- 双 Agent 协同（LangGraph 编排）
- 多粒度检索：库 → 表 → 列 → 值
- 智能 Schema 压缩（主键补全 + 外键血缘补全 + 极简 DDL）
- 多候选生成（Self-Consistency）+ 数据库报错驱动的自我修正
- 多库路由与方言补丁

## 2. 当前进度

| 检查点 | 内容 | 状态 | 提交 |
|---|---|---|---|
| CP1 | 项目骨架、配置、LLM 客户端 | ✅ | `9c8ae6f` |
| CP2 | 演示数仓、描述符、业务口径、方言补丁 | ✅ | `a97a868` |
| CP3 | catalog 元数据目录 | ✅ | `fc6be3e` |
| CP4 | Schema 压缩 | ✅ | `72769d9` |
| CP5 | 数据库沙盒（含安全加固） | ✅ | `4f96b23` / `3e08d15` |
| CP6 | 检索基础件：embedder / BM25 / RRF | ✅ | `4ffc3ab` |
| CP7 | 检索层：schema_retriever / value_index / business_kb / 路由索引 / build_index | ⬜ | — |
| CP8 | `state.py` + `prompts.py` | ⬜ | — |
| CP9 | Router + Analyzer 节点 | ⬜ | — |
| CP10 | Coder + Executor + Evaluator 节点 | ⬜ | — |
| CP11 | `graph.py` + FakeLLM 整图测试 | ⬜ | — |
| CP12 | CLI、README、真实 API 端到端验证 | ⬜ | — |

测试现状：**66 passed**（全部离线，不调用真实 LLM）。

## 3. 快速开始

Windows PowerShell / Git Bash：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python data/build_demo_dbs.py    # 生成两个演示库与注释 sidecar，约 3 秒
.venv/Scripts/python -m pytest -q              # 66 passed
```

Linux / macOS 把 `.venv/Scripts/python` 换成 `.venv/bin/python`。

生成物（已在 `.gitignore`）：

- `data/sales.sqlite`：电商销售域，9 张核心表 + 20 张噪声表
- `data/logs.duckdb`：APP 埋点域，2 张表
- `data/schema/*.json`：表/字段中文注释 sidecar

## 4. 目录与模块职责

```
src/text2sql/
  config.py        Settings（.env / 环境变量）；角色级 LLM 接口覆盖；TODAY；MAX_REPAIR_ROUNDS
  llm.py           ChatModel 协议 + OpenAIChatModel（chat / sample 并发）+ ping 自检
  catalog.py       元数据输入层：SQLite/DuckDB 抽取表、列、类型、PK/FK，合并 sidecar 注释
  compression.py   FK 图、BFS 最短路、主键/桥接补全、极简 DDL 渲染、token 估算
  sandbox.py       只读执行：AST 白名单 + 函数黑名单 + 表名白名单 + EXPLAIN/LIMIT + 超时
  retrieval/
    embedder.py    BGEEmbedder（惰性加载）/ APIEmbedder / HashingEmbedder
    bm25.py        jieba 分词 + BM25Index
    fusion.py      RRF 融合
data/
  build_demo_dbs.py          演示库生成器（固定随机种子）
  descriptors/*.json         库描述符（PDF 的 Database Descriptor）
  dialects/*.md              方言补丁
  knowledge/business_glossary.md  业务口径与指标字典
tests/                       66 个离线单测
```

依赖方向（单向，无环）：

```
config ← llm
config ← catalog ← compression
config ← retrieval/*
sandbox 独立（sqlglot / sqlite3 / duckdb）
```

## 5. 关键接口契约（接手时最重要）

这些函数/类**已经可用**，后续节点应直接复用，不要重写：

```python
# 1) 元数据
from text2sql.catalog import load_database
db = load_database("data/descriptors/sales_sqlite.json")
db.tables            # {表名: TableInfo}，含全部物理表（含噪声表）
db.business_tables   # {表名: TableInfo}，仅描述符声明的核心表
db.dialect           # "sqlite" / "duckdb"，可直接给 sqlglot 和沙盒
db.domain, db.special_rules
db.table("orders").render()          # 极简 DDL 文本

# 2) 压缩：seeds 就是"多粒度检索"的产物
from text2sql.compression import compress_schema
result = compress_schema(db, {"users": ["asset_amount"],
                              "customer_segment": ["segment_name"]})
result.schema_text       # 注入 Analyzer / Coder 的 schema
result.tables            # {表: (列, ...)}，保留列，供调试
result.full_tokens / result.compressed_tokens / result.ratio / result.summary

# 3) 执行
from text2sql.sandbox import Sandbox, check_sql, UnsafeQueryError
box = Sandbox(db.descriptor.path, db.dialect,
              allowed_tables=set(db.tables), timeout=10.0, preview_limit=100)
r = box.execute(sql)     # QueryResult(ok, columns, rows, error, elapsed_ms)
                         # 失败不抛异常，error 直接作为自我修正的 error_msg

# 4) 检索基础件
from text2sql.retrieval.embedder import build_embedder, HashingEmbedder
from text2sql.retrieval.bm25 import BM25Index
from text2sql.retrieval.fusion import rrf
emb = build_embedder()                 # 按 EMBEDDING_BACKEND 选择 bge / api
bm = BM25Index({"sales_record": "销售流水 销售流水事实表", ...})
bm.search("销售额", top_k=10)          # [(doc_id, score)]
rrf([dense_ids, bm25_ids], k=60)       # [(doc_id, score)]，按融合分排序
emb.encode(["文本"]).shape              # (n, dim)，已 L2 归一
```

## 6. 数据流（已实现部分标 ✅）

```
用户问题
  → Router（未实现：descriptor 向量匹配 + 加载 dialects/*.md）
  → Analyzer（未实现节点；零件已就绪）
       ✅ 业务知识 RAG（CP7：business_kb.py）
       ✅ 值检索（CP7：value_index.py）
       ✅ 表级召回：BGE-M3 向量 + BM25 → RRF（CP7：schema_retriever.py）
       ✅ 列级精排（CP7）
       ✅ Schema 压缩（CP4）
       ⬜ Query 改写 / 实体扩展（CP8/9：prompts.py + analyzer.py）
       ⬜ Execution Plan（CP8/9）
  → Coder（未实现：3 条候选并发、T=0.7；修复轮 T=0.2）
  → 沙盒（✅ CP5）：安全检查 → EXPLAIN → LIMIT 100 预览 → 报错/结果
  → Evaluator（未实现：结果归一化 + 多数投票 + LLM-as-judge）
  → 输出
```

## 7. 设计决策记录

| 决策 | 原因 |
|---|---|
| 用 catalog 从系统表抽元数据，而不是 sqlglot 解析原始 DDL 文本 | 索引/默认值/字符集从源头就不采集，等价于 PDF 的 AST 裁剪，且不依赖 sqlglot 内部结构 |
| 演示库用 SQLite + DuckDB | 零依赖、秒级重建；两种方言足以验证 Router 与方言补丁；MySQL/Oracle 只需新增 catalog 抽取函数 |
| 值检索不用 Elasticsearch | 演示规模用进程内倒排索引 + rapidfuzz 足够；接口保留，日后可替换 |
| 3 条候选用线程池独立请求，不传 `n` | 各厂商对 `n` 的支持不一致 |
| 首次生成 + 最多 3 轮修复（可配置） | 以简历口径为准，代码里 `retry_count >= 3` 实际只有 2 次修复，已统一 |
| 修复 prompt 额外携带"失败的 SQL" | PDF 模板只给了报错和 schema，模型看不到自己写错的代码 |
| 沙盒是逻辑隔离而非容器 | 只读连接是硬边界；AST 黑名单 + DuckDB `enable_external_access=false` + 超时；容器化留给部署层 |

## 8. 与 PDF 的差异清单

1. **Schema 压缩**：没有 `compress_ddl(原始DDL文本)` 的 sqlglot 实现，改为结构化元数据选择；输出格式与 PDF 一致。
2. **值检索**：Elasticsearch → 进程内倒排索引（CP7 实现）。
3. **跨库联邦查询**：本期不做，Router 只做单库路由 + 方言补丁。
4. **微调**：SFT / DPO / LlamaFactory 不在本期范围。
5. **模型**：本机 8GB 显存无法部署 72B / 14B，统一走 OpenAI 兼容 API（默认 DashScope）。
6. **重试次数**：按简历口径"3 轮修复"实现，而不是示例代码里的 2 次。

## 9. 已知限制与风险

- **沙盒不是真隔离**：与主程序同进程；SQLite 侧没有内存硬限；能读当前库的全部表和列（无列级脱敏）；执行的是主库文件。生产部署需要：只读备库 + 独立只读账号 + 容器内执行 + 列级权限。
- **演示数据是模拟的**：固定种子可复现，但不代表真实业务分布；"爆款"等问题的数量级很小。
- **本地向量模型依赖尚未安装**：`sentence-transformers` / `faiss-cpu` 在可选依赖 `local-embedding` 中，torch + 模型约 3～4GB。CP7 建索引前必须决定：本机 BGE-M3，还是 `EMBEDDING_BACKEND=api`。
- **模型型号未定**：`ANALYZER_MODEL` / `CODER_MODEL` 还是占位值，需要按实际可用的 DashScope 型号填写。
- **日期敏感**：数据截止 2026-11-07；"最近 30 天"等口径随系统日期漂移，复现实验时用 `TODAY=2026-11-08` 固定。

## 10. 下一步（CP7–CP12）

- **CP7 检索层**：`business_kb.py`（Markdown 分块 + FAISS）、`value_index.py`（维度枚举值倒排 + 模糊匹配）、`schema_retriever.py`（表级 dense+BM25 → RRF → Top10；列级打分 → 每表 Top-N、全局约 30；输出 `dict[表, list[列]]` 供 `compress_schema`）、`db_router_index.py`、`scripts/build_index.py`（索引落盘 `data/index/`）。
- **CP8**：`state.py`（含 trace、attempts、candidates）、`prompts.py`（改写 / 规划 / 生成 / 修复 / 判优；中文）。
- **CP9**：Router + Analyzer 节点。
- **CP10**：Coder（3 候选并发）、Executor（调沙盒 + 未知列放宽 schema）、Evaluator（结果归一化、多数投票、LLM 判优）。
- **CP11**：LangGraph 组装；FakeLLM 覆盖"首次成功 / 第 3 轮修复成功 / 全部失败"三条路径。
- **CP12**：CLI（rich 展示每一步）、README、真实 API 端到端验证（报告生成、数据探查、指标归因等场景）。

## 11. 待决定事项

1. DashScope 上 Analyzer / Coder 的具体模型型号。
2. 向量模型：本地 BGE-M3 还是 API embedding。
3. GitHub 仓库可见性（建议私有）。

## 12. 踩坑记录（Windows / 当前依赖版本）

- `openai` 3.46 使用 **`httpx2`**，不是 `httpx`；自定义 `http_client` 与测试 MockTransport 都要用 `httpx2`。
- DuckDB `con.execute()` 返回连接本身，取结果必须 `.fetchall()` / `.fetchmany()`。
- `duckdb_constraints()` 的列名是 `schema_name`，`information_schema.columns` 是 `table_schema`。
- sqlglot 对 `read_parquet` / `read_csv` 有专门节点，`.name` 不是函数名；`Anonymous` 才用 `.name`，其余用 `sql_name()`。
- BM25 在小语料里 idf 可能恰好为 0，不能拿 `score > 0` 当相关判据。
- Windows 控制台默认 GBK：脚本输出中文前 `sys.stdout.reconfigure(encoding="utf-8")`。
- `data/build_demo_dbs.py` 用 CSV + `COPY` 灌 DuckDB；`executemany` 10 万行要 5 分钟。
