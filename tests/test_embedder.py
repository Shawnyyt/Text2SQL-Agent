"""CP6：向量编码器（哈希 / API / 工厂 / BGE 惰性加载）。"""

import json

import httpx2 as httpx
import numpy as np
import pytest

from text2sql.config import Settings
from text2sql.retrieval.embedder import (
    APIEmbedder,
    BGEEmbedder,
    HashingEmbedder,
    build_embedder,
    cosine,
    l2_normalize,
)


def make_settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_hashing_embedder_is_deterministic_and_normalized():
    emb = HashingEmbedder(dim=64)
    v1 = emb.encode(["华东区 销售额", "用户登录"])
    v2 = emb.encode(["华东区 销售额", "用户登录"])
    assert np.allclose(v1, v2)
    assert v1.shape == (2, 64)
    assert np.allclose(np.linalg.norm(v1, axis=1), 1.0)
    assert not np.allclose(v1[0], v1[1])
    assert cosine(v1[0], v1).shape == (2,)


def test_cosine_with_empty_matrix():
    assert cosine(np.ones(4, dtype=np.float32),
                  np.zeros((0, 4), dtype=np.float32)).shape == (0,)


def test_api_embedder_batches_and_normalizes():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        data = [
            {"object": "embedding", "index": i,
             "embedding": [float(i + 1), 0.0, 2.0]}
            for i in range(len(body["input"]))
        ]
        return httpx.Response(200, json={
            "object": "list", "data": data, "model": body["model"],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        })

    emb = APIEmbedder(
        "http://mock/v1", "k", "m", batch_size=10,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    vectors = emb.encode([f"t{i}" for i in range(12)])
    assert [len(c["input"]) for c in calls] == [10, 2]
    assert all(c["model"] == "m" for c in calls)
    assert vectors.shape == (12, 3)
    assert emb.dim == 3
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)


def test_api_embedder_empty_input():
    emb = APIEmbedder("http://mock/v1", "k", "m")
    assert emb.encode([]).shape == (0, 0)


def test_build_embedder_api_and_unknown_backend():
    s = make_settings(embedding_backend="api", llm_base_url="http://mock/v1",
                      llm_api_key="k", embedding_api_model="text-embedding-v3")
    emb = build_embedder(s)
    assert isinstance(emb, APIEmbedder) and emb.model == "text-embedding-v3"

    with pytest.raises(ValueError, match="未知的 embedding 后端"):
        build_embedder(make_settings(embedding_backend="magic"))


def test_bge_embedder_is_lazy():
    emb = BGEEmbedder(model_name="BAAI/bge-m3")
    assert emb._model is None and emb.dim is None
    assert build_embedder(make_settings(embedding_backend="bge", embedding_device="cpu")) \
        .model_name == "BAAI/bge-m3"


def test_l2_normalize_handles_zero_rows():
    m = l2_normalize(np.array([[0.0, 0.0], [3.0, 4.0]], dtype=np.float32))
    assert np.allclose(m, [[0.0, 0.0], [0.6, 0.8]])
