"""LLM 客户端抽象层。

当前接 DeepSeek（OpenAI 兼容接口），通过 requests 实现流式。
抽象为 Protocol，未来可换 Claude / 通义 / 本地模型而不动上层编排。
"""
from __future__ import annotations

import json
from typing import Dict, Iterator, List, Protocol, runtime_checkable

import requests


@runtime_checkable
class LLMClient(Protocol):
    """上层（Agent）只依赖这个协议。"""

    def stream_chat(self, system: str, history: List[Dict[str, str]]) -> Iterator[str]:
        """流式返回回复文本片段。history 为 [{role, content}]（不含 system）。"""
        ...


class DeepSeekLLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "deepseek-chat",
        temperature: float = 0.9,
        max_tokens: int = 1024,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    def stream_chat(self, system: str, history: List[Dict[str, str]]) -> Iterator[str]:
        messages = [{"role": "system", "content": system}] + list(history)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with requests.post(
            self.url, json=payload, headers=headers, stream=True, timeout=self.timeout
        ) as resp:
            if resp.status_code != 200:
                body = resp.text[:500]
                raise RuntimeError(f"DeepSeek 返回 {resp.status_code}: {body}")
            for raw in resp.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if not raw.startswith("data:"):
                    continue
                data = raw[len("data:") :].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                try:
                    delta = obj["choices"][0]["delta"].get("content", "")
                except (KeyError, IndexError, TypeError):
                    continue
                if delta:
                    yield delta
