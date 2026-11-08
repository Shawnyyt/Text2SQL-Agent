"""LLM 客户端：对 OpenAI 兼容接口的薄封装。

节点只依赖 ChatModel 协议（chat / sample），单测里用 FakeLLM 实现同一协议即可替换。
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

import httpx2 as httpx
from openai import OpenAI

from text2sql.config import LLMEndpoint, Settings, get_settings

Message = dict[str, str]


class ChatModel(Protocol):
    def chat(self, messages: list[Message], *, temperature: float = 0.0,
             top_p: float = 1.0) -> str: ...

    def sample(self, messages: list[Message], *, n: int, temperature: float,
               top_p: float = 1.0) -> list[str]: ...


def user_msg(prompt: str, system: str | None = None) -> list[Message]:
    """把单段 prompt 包装成 messages。"""
    msgs: list[Message] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


class OpenAIChatModel:
    def __init__(self, endpoint: LLMEndpoint, *, timeout: float = 60.0,
                 max_retries: int = 2, http_client: httpx.Client | None = None):
        self.model = endpoint.model
        self._client = OpenAI(
            base_url=endpoint.base_url,
            api_key=endpoint.api_key,
            timeout=timeout,
            max_retries=max_retries,
            http_client=http_client,
        )

    def chat(self, messages: list[Message], *, temperature: float = 0.0,
             top_p: float = 1.0) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
        )
        return resp.choices[0].message.content or ""

    def sample(self, messages: list[Message], *, n: int, temperature: float,
               top_p: float = 1.0) -> list[str]:
        """并发发 n 次独立请求，不依赖服务商对 `n` 参数的支持。

        任一请求失败即抛出异常，由调用方决定如何处理。
        """
        with ThreadPoolExecutor(max_workers=n) as pool:
            futures = [
                pool.submit(self.chat, messages, temperature=temperature, top_p=top_p)
                for _ in range(n)
            ]
            return [f.result() for f in futures]


def build_chat_model(role: str, settings: Settings | None = None) -> OpenAIChatModel:
    """按角色（analyzer / coder）构造客户端。"""
    s = settings or get_settings()
    return OpenAIChatModel(
        s.endpoint(role), timeout=s.llm_timeout, max_retries=s.llm_max_retries
    )


def ping(role: str, settings: Settings | None = None) -> str:
    """连通性自检：发一条极短请求，返回模型回复；失败时抛出原始异常。"""
    model = build_chat_model(role, settings)
    return model.chat(user_msg("只回复 OK 两个字母。"), temperature=0.0)
