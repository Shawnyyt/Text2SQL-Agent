"""全局配置：从环境变量 / .env 读取，所有模块通过 get_settings() 取用。"""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class LLMEndpoint(BaseModel):
    """某个 Agent 角色实际使用的接口参数。"""

    base_url: str
    api_key: str
    model: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- LLM（OpenAI 兼容）----
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: str = ""
    llm_timeout: float = 60.0
    llm_max_retries: int = 2

    analyzer_model: str = ""
    analyzer_base_url: str | None = None
    analyzer_api_key: str | None = None

    coder_model: str = ""
    coder_base_url: str | None = None
    coder_api_key: str | None = None

    # ---- 向量检索 ----
    embedding_backend: str = "bge"  # bge=本地 BGE-M3；api=OpenAI 兼容 embeddings
    embedding_model: str = "BAAI/bge-m3"
    embedding_device: str = "auto"  # auto / cuda / cpu
    embedding_max_length: int = 512
    embedding_api_model: str = "text-embedding-v3"
    embedding_api_base_url: str | None = None
    embedding_api_key: str | None = None

    # ---- 检索参数 ----
    retrieval_top_tables: int = 5
    retrieval_cols_per_table: int = 4
    retrieval_max_cols: int = 30
    retrieval_business_kb_top_k: int = 3

    # ---- 业务流程 ----
    # 首次生成之后最多修复几轮（共识 Q9：首次 + 最多 3 轮修复）
    max_repair_rounds: int = 3
    # prompt 中的“今天”；不填则取系统日期
    today: date | None = None

    # ---- 路径 ----
    data_dir: Path = PROJECT_ROOT / "data"

    def endpoint(self, role: str) -> LLMEndpoint:
        """返回 analyzer / coder 的接口参数，未单独配置的字段沿用 LLM_*。"""
        if role not in ("analyzer", "coder"):
            raise ValueError(f"未知角色: {role}")
        model = getattr(self, f"{role}_model")
        if not model:
            raise ValueError(f"未配置 {role.upper()}_MODEL")
        return LLMEndpoint(
            base_url=getattr(self, f"{role}_base_url") or self.llm_base_url,
            api_key=getattr(self, f"{role}_api_key") or self.llm_api_key,
            model=model,
        )

    def resolved_today(self) -> date:
        return self.today or date.today()


@lru_cache
def get_settings() -> Settings:
    return Settings()
