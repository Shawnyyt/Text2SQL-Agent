"""运行时依赖容器：把索引、数据库元数据、LLM 一次性装配好，供各节点共享。

节点函数本身不负责加载，只从 Runtime 取依赖，方便测试时注入 FakeLLM / 哈希向量。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from text2sql.catalog import DatabaseDescriptor, DatabaseInfo, load_database
from text2sql.config import Settings, get_settings
from text2sql.llm import ChatModel, build_chat_model
from text2sql.retrieval.business_kb import BusinessKB
from text2sql.retrieval.db_router_index import RouterIndex
from text2sql.retrieval.embedder import Embedder, build_embedder
from text2sql.retrieval.schema_retriever import SchemaRetriever
from text2sql.retrieval.value_index import ValueIndex


@dataclass
class Runtime:
    settings: Settings
    embedder: Embedder
    analyzer_llm: ChatModel
    coder_llm: ChatModel
    descriptors: dict[str, DatabaseDescriptor]
    databases: dict[str, DatabaseInfo]
    router: RouterIndex
    business_kb: BusinessKB
    retrievers: dict[str, SchemaRetriever]
    value_indexes: dict[str, ValueIndex]
    dialect_dir: Path

    @classmethod
    def from_settings(cls, settings: Settings | None = None, *,
                      embedder: Embedder | None = None,
                      analyzer_llm: ChatModel | None = None,
                      coder_llm: ChatModel | None = None) -> "Runtime":
        s = settings or get_settings()
        data_dir = s.data_dir
        index_root = data_dir / "index"
        if not (index_root / "router").exists():
            raise FileNotFoundError(
                f"检索索引不存在: {index_root}，请先运行 python scripts/build_index.py")

        desc_paths = sorted((data_dir / "descriptors").glob("*.json"))
        descriptors: dict[str, DatabaseDescriptor] = {}
        databases: dict[str, DatabaseInfo] = {}
        for path in desc_paths:
            db = load_database(path)
            descriptors[db.db_id] = db.descriptor
            databases[db.db_id] = db

        return cls(
            settings=s,
            embedder=embedder or build_embedder(s),
            analyzer_llm=analyzer_llm or build_chat_model("analyzer", s),
            coder_llm=coder_llm or build_chat_model("coder", s),
            descriptors=descriptors,
            databases=databases,
            router=RouterIndex.load(index_root / "router"),
            business_kb=BusinessKB.load(index_root / "business"),
            retrievers={db_id: SchemaRetriever.load(index_root / db_id)
                        for db_id in databases},
            value_indexes={db_id: ValueIndex.load(index_root / db_id / "values.json")
                           for db_id in databases},
            dialect_dir=data_dir / "dialects",
        )

    def dialect_markdown(self, descriptor: DatabaseDescriptor) -> str:
        path = self.dialect_dir / f"{descriptor.dialect}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "\n".join(f"- {rule}" for rule in descriptor.special_rules)
