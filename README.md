# 企业级多智能体 Text2SQL

基于 LangGraph 双 Agent 协同的自然语言查询引擎：

```
Router（语义路由 + 方言补丁）
  → Analyzer（改写/实体抽取 → 业务知识 + 值映射 + 表列检索 → Schema 压缩 → 执行计划）
  → Coder（首轮 3 候选并发生成；失败时带报错修复，最多 3 轮）
  → Executor（只读沙盒：AST 安全检查 → EXPLAIN → LIMIT 执行）
  → Evaluator（结果一致性投票 / LLM 判优）
```

完整设计、接口契约与踩坑记录见 **[HANDOFF.md](HANDOFF.md)**。

## 当前状态

| 模块 | 状态 |
|---|---|
| 演示数仓 / 描述符 / 业务口径 / 方言补丁 | ✅ |
| 元数据目录（SQLite / DuckDB） | ✅ |
| Schema 压缩（主键 + FK 血缘补全 + 极简 DDL） | ✅ |
| 数据库沙盒（只读 + AST 白名单 + 函数黑名单 + 超时） | ✅ |
| 多粒度检索（库 / 表 / 列 / 值）+ BGE-M3 索引 | ✅ |
| LangGraph 完整链路 + 修复/判优 | ✅ |
| CLI / README | ✅ |
| 真实 LLM 端到端验证 | ⏳ 待配置模型 |

测试：108 个离线用例（`pytest -q`），不调用真实 LLM。

## 快速开始

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python data/build_demo_dbs.py      # 生成演示库（约 3 秒）
cp .env.example .env                              # 填写 LLM 配置
.venv/Scripts/python scripts/build_index.py       # 构建检索索引
.venv/Scripts/python -m pytest -q
```

Linux / macOS 把 `.venv/Scripts/python` 换成 `.venv/bin/python`。

### 本地向量模型（BGE-M3）

```bash
.venv/Scripts/python -m pip install torch --index-url https://download.pytorch.org/whl/cu126
.venv/Scripts/python -m pip install sentence-transformers
HF_ENDPOINT=https://hf-mirror.com .venv/Scripts/python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-m3', local_dir='models/bge-m3', allow_patterns=['*.json','*.bin','*.txt','*.model','*.spm'])"
```

`.env` 中设置 `EMBEDDING_MODEL=D:/study/text2sql/models/bge-m3`；也可改用 `EMBEDDING_BACKEND=api` 走 OpenAI 兼容的 embeddings 接口。

## 使用

```bash
text2sql ask "上个月华东区卖得最好的三个爆款"
text2sql ask "昨天 APP 的点击转化率" --today 2026-11-08
text2sql ask "高净值客户有多少" --json
text2sql build-index     # 重建 data/index/
text2sql ping            # 检查两个 LLM 接口连通性
```

`ask` 会打印：路由候选、改写与实体、业务口径、值映射、压缩后的 schema、候选 SQL 及执行状态、最终 SQL 与结果表。

## 配置（`.env`）

| 变量 | 说明 |
|---|---|
| `LLM_BASE_URL` / `LLM_API_KEY` | OpenAI 兼容接口（默认 DashScope） |
| `ANALYZER_MODEL` / `CODER_MODEL` | 两个 Agent 的模型 |
| `CODER_BASE_URL` / `CODER_API_KEY` | 可选，Coder 单独走另一个服务 |
| `EMBEDDING_BACKEND` | `bge`（本地）或 `api` |
| `EMBEDDING_MODEL` | BGE-M3 模型名或本地路径 |
| `MAX_REPAIR_ROUNDS` | 首次生成后的最大修复轮数（默认 3） |
| `CANDIDATE_COUNT` | 首轮候选数（默认 3） |
| `TODAY` | 固定“今天”，便于复现实验 |
| `RETRIEVAL_TOP_TABLES` / `RETRIEVAL_COLS_PER_TABLE` / `RETRIEVAL_MAX_COLS` | 召回规模 |

## 目录

```
src/text2sql/
  graph.py         LangGraph 编排与条件边
  nodes/           router / analyzer / coder / executor / evaluator
  runtime.py       依赖容器（索引、元数据、LLM）
  catalog.py       元数据目录           compression.py  Schema 压缩
  sandbox.py       只读执行沙盒         retrieval/      四级检索与向量索引
  prompts.py       提示词模板           state.py        全局状态
  cli.py           命令行
data/
  build_demo_dbs.py / descriptors/ / dialects/ / knowledge/
```

## 已知限制

- 沙盒是逻辑隔离（只读连接 + AST 白名单 + 资源限制），不是容器；生产需只读备库、独立账号、容器执行。
- 演示数据为固定种子生成的模拟数据。
- 真实 LLM 效果尚未验证，需在 `.env` 配置 `ANALYZER_MODEL` / `CODER_MODEL` 后跑 `text2sql ping` 与端到端问题。
