"""LLM 客户端抽象层。

当前接 DeepSeek（OpenAI 兼容接口），通过 requests 实现流式。
抽象为 Protocol，未来可换 Claude / 通义 / 本地模型而不动上层编排。

重试策略（审计补强）：连接建立失败 / 超时属瞬时抖动，在**尚未产出任何
内容**前做指数退避重试（默认 2 次：等 1s、2s）；一旦已向调用方 yield 过
内容就不再重试（流式无法回收已输出的文本，重试会造成重复）。非 200 的
HTTP 错误（鉴权/限流等业务性失败）不重试，直接抛给上层降级。
"""
from __future__ import annotations

import json
import time
from typing import Dict, Iterator, List, Protocol, runtime_checkable

import requests

# 网络层瞬时异常：值得重试
_TRANSIENT = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)
_MAX_RETRIES = 2


@runtime_checkable
class LLMClient(Protocol):
    """上层（Agent）只依赖这个协议。"""

    def stream_chat(self, system: str, history: List[Dict[str, str]]) -> Iterator[str]:
        """流式返回回复文本片段。history 为 [{role, content}]（不含 system）。"""
        ...

    def chat(
        self,
        system: str,
        history: List[Dict[str, str]],
        *,
        temperature: "float | None" = None,
        max_tokens: "int | None" = None,
    ) -> str:
        """非流式：返回模型完整回复文本（结构化 JSON 抽取用，如 Step 2.7 记忆抽取）。

        与 stream_chat 的区别：一次性返回整段，便于解析 JSON / 工具调用结果，
        而不是逐 token 流式。具体实现（DeepSeek 等）自行决定如何聚合。
        """
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
        for attempt in range(_MAX_RETRIES + 1):
            produced = False
            try:
                for delta in self._stream_once(system, history):
                    produced = True
                    yield delta
                return
            except _TRANSIENT:
                # 已产出内容不重试（会重复）；重试次数耗尽则把异常交上层降级
                if produced or attempt == _MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)  # 退避 1s / 2s

    def _stream_once(self, system: str, history: List[Dict[str, str]]) -> Iterator[str]:
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

    def chat(
        self,
        system: str,
        history: List[Dict[str, str]],
        *,
        temperature: "float | None" = None,
        max_tokens: "int | None" = None,
    ) -> str:
        """非流式单发：记忆抽取用（ExtractionEngine）。

        与 stream_chat 共用同一端点（/chat/completions），仅 stream=False 一次性
        取回完整回复，便于解析 JSON 候选数组。任何非 200 / 结构异常都抛 RuntimeError，
        由上层（ExtractionEngine.extract）静默降级为 []——抽取失败绝不崩对话。

        注意：本方法只服务于「结构化抽取」，不用于陪聊回复（回复走 stream_chat）。
        连接类瞬时异常同样做退避重试（非流式幂等，无「已产出」约束）。
        """
        messages = [{"role": "system", "content": system}] + list(history)
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else float(temperature),
            "max_tokens": self.max_tokens if max_tokens is None else int(max_tokens),
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        resp = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    self.url, json=payload, headers=headers, timeout=self.timeout
                )
                break
            except _TRANSIENT:
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)
        if resp.status_code != 200:
            body = resp.text[:500]
            raise RuntimeError(f"DeepSeek 返回 {resp.status_code}: {body}")
        try:
            obj = resp.json()
        except ValueError:
            raise RuntimeError(f"DeepSeek 返回非 JSON 响应: {resp.text[:200]}")
        try:
            return obj["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"DeepSeek 响应结构异常: {obj}") from exc
