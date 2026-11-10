"""向量编码器：本地 BGE-M3 与 OpenAI 兼容接口两套实现。

统一协议 Embedder.encode(texts) -> np.ndarray，行向量已做 L2 归一化，
因此检索侧可以直接用点积当余弦相似度。

BGE 模型采用惰性加载：构造对象不会加载模型，首次 encode 才加载，
这样单测和没有模型缓存的机器也能安全导入模块。
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, Sequence

import numpy as np

from text2sql.config import Settings, get_settings


class Embedder(Protocol):
    dim: int | None

    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


class BGEEmbedder:
    """本地 BGE-M3（sentence-transformers），默认自动选择 GPU。"""

    def __init__(self, model_name: str = "BAAI/bge-m3", device: str = "auto",
                 max_length: int = 512, batch_size: int = 16):
        self.model_name = model_name
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size
        self.dim: int | None = None
        self._model = None

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 取决于安装环境
            raise RuntimeError(
                "本地向量模型需要额外依赖，请执行："
                "pip install -e \".[local-embedding]\""
            ) from exc
        device = self.device
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"
        model = SentenceTransformer(self.model_name, device=device)
        model.max_seq_length = self.max_length
        self._model = model
        self.dim = int(model.get_sentence_embedding_dimension())
        return model

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        model = self._load()
        vectors = model.encode(
            list(texts), batch_size=self.batch_size, normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)


class APIEmbedder:
    """OpenAI 兼容的 embeddings 接口（如 DashScope text-embedding-v3）。"""

    def __init__(self, base_url: str, api_key: str, model: str, *,
                 batch_size: int = 10, timeout: float = 60.0,
                 max_retries: int = 2, http_client=None):
        from openai import OpenAI

        kwargs = {"base_url": base_url, "api_key": api_key,
                  "timeout": timeout, "max_retries": max_retries}
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = OpenAI(**kwargs)
        self.model = model
        self.batch_size = batch_size
        self.dim: int | None = None

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        items = list(texts)
        if not items:
            return np.zeros((0, self.dim or 0), dtype=np.float32)
        vectors: list[list[float]] = []
        for start in range(0, len(items), self.batch_size):
            batch = items[start:start + self.batch_size]
            resp = self._client.embeddings.create(model=self.model, input=batch)
            ordered = sorted(resp.data, key=lambda d: d.index)
            vectors.extend([d.embedding for d in ordered])
        matrix = np.asarray(vectors, dtype=np.float32)
        self.dim = int(matrix.shape[1])
        return l2_normalize(matrix)


class HashingEmbedder:
    """离线确定性向量（字符 n-gram 哈希）。仅用于测试和无模型环境。"""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            normalized = text.lower()
            for n in (1, 2, 3):
                for i in range(max(1, len(normalized) - n + 1)):
                    gram = normalized[i:i + n]
                    h = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                    matrix[row, int.from_bytes(h, "big") % self.dim] += 1.0
        return l2_normalize(matrix)


def build_embedder(settings: Settings | None = None) -> Embedder:
    s = settings or get_settings()
    backend = s.embedding_backend.lower()
    if backend == "api":
        return APIEmbedder(
            base_url=s.embedding_api_base_url or s.llm_base_url,
            api_key=s.embedding_api_key or s.llm_api_key,
            model=s.embedding_api_model,
        )
    if backend == "bge":
        return BGEEmbedder(model_name=s.embedding_model, device=s.embedding_device,
                           max_length=s.embedding_max_length)
    raise ValueError(f"未知的 embedding 后端: {s.embedding_backend}")


def cosine(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    """归一化向量的点积即余弦相似度。"""
    if matrix.size == 0:
        return np.zeros(0, dtype=np.float32)
    return matrix @ query_vec
