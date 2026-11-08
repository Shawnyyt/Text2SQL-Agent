"""CP1：配置解析与 LLM 客户端（不联网，用 httpx.MockTransport 打桩）。"""

import json
from datetime import date

import httpx2 as httpx
import pytest

from text2sql.config import Settings
from text2sql.llm import OpenAIChatModel, user_msg


def make_settings(**kw) -> Settings:
    # _env_file=None：不读取本地 .env，保证测试结果与机器无关
    return Settings(_env_file=None, **kw)


def test_endpoint_falls_back_to_shared_llm_settings():
    s = make_settings(llm_base_url="http://shared/v1", llm_api_key="k",
                      analyzer_model="a", coder_model="c",
                      coder_base_url="http://vllm/v1")
    a = s.endpoint("analyzer")
    c = s.endpoint("coder")
    assert (a.base_url, a.api_key, a.model) == ("http://shared/v1", "k", "a")
    assert (c.base_url, c.api_key, c.model) == ("http://vllm/v1", "k", "c")


def test_endpoint_requires_model_and_valid_role():
    s = make_settings()
    with pytest.raises(ValueError, match="CODER_MODEL"):
        s.endpoint("coder")
    with pytest.raises(ValueError, match="未知角色"):
        s.endpoint("judge")


def test_today_override():
    assert make_settings(today="2026-01-31").resolved_today() == date(2026, 1, 31)
    assert make_settings().resolved_today() == date.today()


def _mock_model(handler) -> OpenAIChatModel:
    from text2sql.config import LLMEndpoint
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIChatModel(LLMEndpoint(base_url="http://mock/v1", api_key="k", model="m"),
                           max_retries=0, http_client=client)


def _completion(content: str) -> dict:
    return {
        "id": "x", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
    }


def test_chat_sends_params_and_returns_content():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(json.loads(req.content))
        return httpx.Response(200, json=_completion("OK"))

    out = _mock_model(handler).chat(user_msg("hi", system="sys"), temperature=0.2, top_p=0.9)
    assert out == "OK"
    assert seen["model"] == "m"
    assert seen["temperature"] == 0.2 and seen["top_p"] == 0.9
    assert [m["role"] for m in seen["messages"]] == ["system", "user"]


def test_sample_makes_n_independent_requests():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(json.loads(req.content))
        return httpx.Response(200, json=_completion(f"SQL{len(calls)}"))

    outs = _mock_model(handler).sample(user_msg("q"), n=3, temperature=0.7, top_p=0.9)
    assert len(outs) == 3 and len(calls) == 3
    assert all("n" not in c for c in calls)  # 不依赖服务商的 n 参数
    assert all(c["temperature"] == 0.7 for c in calls)


def test_sample_propagates_errors():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    with pytest.raises(Exception):
        _mock_model(handler).sample(user_msg("q"), n=3, temperature=0.7)
