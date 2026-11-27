"""LLM 客户端抽象层。

当前接 DeepSeek（OpenAI 兼容接口），通过 requests 实现流式。
抽象为 Protocol，未来可换 Claude / 通义 / 本地模型而不动上层编排。

重试策略（审计补强）：连接建立失败 / 超时属瞬时抖动，在**尚未产出任何
内容**前做指数退避重试（默认 2 次：等 1s、2s）；一旦已向调用方 yield 过
内容就不再重试（流式无法回收已输出的文本，重试会造成重复）。非 200 的
HTTP 错误（鉴权/限流等业务性失败）不重试，直接抛给上层降级。

2.x Phase 1 批1 新增（docs/2x/LLM_INTEGRATION.md）：
- ToolCall / LLMResponse / LLMError 数据结构与两层错误模型
- chat_with_tools：非流式 Function Calling（旧 stream_chat/chat 逐字保留）
- cancel Event 贯穿请求与退避；usage 追加 logs/metrics.jsonl；日志脱敏
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Protocol, runtime_checkable

import requests

logger = logging.getLogger(__name__)

# 网络层瞬时异常：值得重试。ChunkedEncodingError 常见于连接在流式传输
# 途中被掐断——只要尚未产出内容（produced 守卫），重试是安全的
_TRANSIENT = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)
_MAX_RETRIES = 2

# 错误 kind → 是否可重试（chat_with_tools 非流式幂等；produced 守卫只约束流式）
_RETRYABLE_KINDS = frozenset({"network", "timeout", "rate_limit", "server"})
# 退避上限：Retry-After 再大也不超过它（防服务端异常值挂死调用方）
_BACKOFF_CAP_S = 60.0
_METRICS_PATH = Path(__file__).resolve().parents[1] / "logs" / "metrics.jsonl"


class LLMError(RuntimeError):
    """两层错误模型的业务层：kind 决定 retryable，文案由调用方定。

    kind: auth | quota | rate_limit | network | timeout | protocol | server | cancelled
    """

    def __init__(self, message: str, kind: str, status: "Optional[int]" = None):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = kind in _RETRYABLE_KINDS


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict  # 已 json.loads；解析失败在客户端层抛 LLMError(protocol)


@dataclass(frozen=True)
class LLMResponse:
    content: "Optional[str]"
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = ""  # 未知值原样保留（前向兼容）
    usage: "Optional[dict]" = None
    model: str = ""
    # ---- Phase 4：DeepSeek 服务端工具（web search / url reading）----
    # 与本地 tool_calls 互斥（官方：同请求混用会 400）。这里原样保留服务端返回，
    # **不**做任何解析成 ToolCall 的动作——它们绝不进本地 Executor。
    server_tool_calls: List[dict] = field(default_factory=list)
    annotations: List[dict] = field(default_factory=list)  # url_citation
    reasoning_content: "Optional[str]" = None


# ---------------------------------------------------------------------------
# 服务端工具（Phase 4）
#
# 事实来源：https://api-docs.deepseek.com/guides/web_tools（2026-11-23 核对）
# - Chat Completions（OpenAI 兼容）可用，但用扩展字段：tools 只写 type（无 name）；
#   响应在 message.server_tool_calls / message.annotations。
# - 不能与本地 function 工具同请求混用（400）；tool_choice 不能指定它们。
# - 页面正文不入 content（data 恒空串），加密存于 signature，多轮必须原样回传。
# - 工具本身不计费，只按 token 计费。
# ---------------------------------------------------------------------------
SERVER_TOOL_WEB_SEARCH = "deepseek_web_search"
SERVER_TOOL_READ_URL = "deepseek_read_url"
_SERVER_TOOL_TYPES = (SERVER_TOOL_WEB_SEARCH, SERVER_TOOL_READ_URL)


def server_tool_declarations(*, web_search: bool, read_url: bool) -> List[dict]:
    """按开关生成服务端工具声明（Chat Completions 形态：只写 type）。

    两者都不开返回空列表——调用方据此拒绝发起「联网」请求，而不是发一个
    没有工具的普通请求冒充联网。
    """
    out: List[dict] = []
    if web_search:
        out.append({"type": SERVER_TOOL_WEB_SEARCH})
    if read_url:
        out.append({"type": SERVER_TOOL_READ_URL})
    return out


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


@runtime_checkable
class ToolCapableLLM(Protocol):
    """Phase 1 新能力接口；上层按需依赖，旧 LLMClient 调用方零感知。"""

    def chat_with_tools(
        self,
        system: str,
        messages: List[dict],
        *,
        tools: "Optional[List[dict]]" = None,
        tool_choice: "str | dict" = "auto",
        temperature: "float | None" = None,
        max_tokens: "int | None" = None,
        cancel: "Optional[threading.Event]" = None,
        thinking: "Optional[dict]" = None,
        response_format: "Optional[dict]" = None,
    ) -> LLMResponse:
        """非流式结构化单发。messages 为完整 role 字典列表（含 assistant.tool_calls
        与 role:tool，尾部追加语义）。tools=None 时省略工具字段（纯 JSON Output 场景）；
        response_format 如 {"type":"json_object"}（prompt 须含 json 字样）。"""
        ...


def _classify_status(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 402:
        return "quota"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "server"
    return "protocol"  # 400/404/413 等请求侧问题


def _record_usage(caller: str, resp: LLMResponse, latency_ms: int) -> None:
    """usage 追加 metrics.jsonl（TurnPipeline 门槛的一部分）。失败绝不影响对话。"""
    try:
        u = resp.usage or {}
        details = u.get("completion_tokens_details") or {}
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "caller": caller,
            "model": resp.model,
            "finish_reason": resp.finish_reason,
            "latency_ms": latency_ms,
            "prompt_tokens": u.get("prompt_tokens"),
            "completion_tokens": u.get("completion_tokens"),
            "total_tokens": u.get("total_tokens"),
            "reasoning_tokens": details.get("reasoning_tokens"),
            "cache_hit_tokens": u.get("prompt_cache_hit_tokens"),
        }
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_METRICS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("metrics 记录失败（不影响对话）", exc_info=True)


def _parse_tool_calls(raw: "Optional[list]") -> List[ToolCall]:
    out: List[ToolCall] = []
    for tc in raw or []:
        try:
            fn = tc["function"]
            args = json.loads(fn.get("arguments") or "{}")
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"tool_calls 结构/参数 JSON 非法: {exc}", "protocol") from exc
        if not isinstance(args, dict):
            raise LLMError("tool_calls arguments 不是 JSON 对象", "protocol")
        out.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args))
    return out


def _interruptible_sleep(seconds: float, cancel: "Optional[threading.Event]") -> None:
    """退避等待可被 cancel 立即打断（借鉴 chat-demo abort 贯穿退避）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            raise LLMError("请求已取消", "cancelled")
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))


class DeepSeekLLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str = "deepseek-flash",
        temperature: float = 0.9,
        max_tokens: int = 1024,
        timeout: int = 30,
        thinking: "Optional[dict]" = None,
    ) -> None:
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        # 实例级思考模式默认（2.x 模型迁移）：None=不发送该字段（服务端默认）；
        # {"type":"disabled"}=关思考（陪聊低延迟）；chat_with_tools 显式参数可覆盖。
        self.thinking = thinking

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
        if self.thinking is not None:
            payload["thinking"] = self.thinking
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
        if self.thinking is not None:
            payload["thinking"] = self.thinking
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(_MAX_RETRIES + 1):
            try:
                # with 确保 retry/解析异常路径也释放连接（抽取是低频调用，
                # 但句柄滞留至 GC 的模式在长期运行下会堆积）
                with requests.post(
                    self.url, json=payload, headers=headers, timeout=self.timeout
                ) as resp:
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
            except _TRANSIENT:
                if attempt == _MAX_RETRIES:
                    raise
                time.sleep(2 ** attempt)

    # ----- 2.x Phase 1 批1：非流式 Function Calling -----

    def chat_with_tools(
        self,
        system: str,
        messages: List[dict],
        *,
        tools: "Optional[List[dict]]" = None,
        tool_choice: "str | dict" = "auto",
        temperature: "float | None" = None,
        max_tokens: "int | None" = None,
        cancel: "Optional[threading.Event]" = None,
        thinking: "Optional[dict]" = None,
        response_format: "Optional[dict]" = None,
    ) -> LLMResponse:
        """非流式 FC / JSON Output 单发（Runtime 循环与抽取引擎的模型请求入口）。

        与 chat() 的区别：可携带 tools、返回结构化 LLMResponse（tool_calls/
        finish_reason/usage）、错误分类为 LLMError、支持 cancel、记录 metrics。
        tools=None 省略工具字段；response_format 透传（JSON Output 批2）。
        重试：可重试 kind（network/timeout/rate_limit/server）退避 2 次，
        退避可被 cancel 打断；429 尊重 Retry-After（上限 60s）。
        取消语义：请求前/退避中检查 Event；在途请求自然结束或超时（批1 限制，
        Runtime 在循环边界取消，见 AGENT_RUNTIME.md）。
        """
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "temperature": self.temperature if temperature is None else float(temperature),
            "max_tokens": self.max_tokens if max_tokens is None else int(max_tokens),
            "stream": False,
        }
        if tools is not None:
            payload["tools"] = list(tools)
            payload["tool_choice"] = tool_choice
        effective_thinking = self.thinking if thinking is None else thinking
        if effective_thinking is not None:
            payload["thinking"] = effective_thinking
        if response_format is not None:
            payload["response_format"] = response_format

        obj, latency_ms = self._post_chat(payload, caller="chat_with_tools", cancel=cancel)
        choice, msg = _extract_choice(obj)
        result = LLMResponse(
            content=msg.get("content"),
            tool_calls=_parse_tool_calls(msg.get("tool_calls")),
            finish_reason=choice.get("finish_reason") or "",
            usage=obj.get("usage"),
            model=obj.get("model", ""),
            reasoning_content=msg.get("reasoning_content"),
        )
        _record_usage("chat_with_tools", result, latency_ms)
        return result

    # ----- 2.x Phase 4：DeepSeek 服务端工具（web search / url reading） -----

    def chat_with_server_tools(
        self,
        system: str,
        messages: List[dict],
        *,
        tools: List[dict],
        thinking: "Optional[dict]" = None,
        temperature: "float | None" = None,
        max_tokens: "int | None" = None,
        cancel: "Optional[threading.Event]" = None,
    ) -> LLMResponse:
        """带服务端工具的请求（非流式）。与 chat_with_tools **互斥**。

        为什么单独一个方法而不是给 chat_with_tools 加参数：
        官方明确「服务端工具不能与 function 工具同请求混用（400）」，分成两个
        入口能让「混用」在调用层面就不可表达，而不是等 API 报错再猜。

        tools 只接受 {"type": "deepseek_web_search"|"deepseek_read_url"}；
        tool_choice 不发送（官方：指名会 400，required 被当 auto）。
        多轮回传：调用方必须把整条 assistant 消息（含 server_tool_calls 的
        kind/signature）原样放回 messages——本方法只负责单次请求。
        """
        decl = self._validate_server_tools(tools)
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}] + list(messages),
            "temperature": self.temperature if temperature is None else float(temperature),
            "max_tokens": self.max_tokens if max_tokens is None else int(max_tokens),
            "stream": False,
            "tools": decl,
        }
        effective_thinking = self.thinking if thinking is None else thinking
        if effective_thinking is not None:
            payload["thinking"] = effective_thinking

        obj, latency_ms = self._post_chat(payload, caller="server_tools", cancel=cancel)
        choice, msg = _extract_choice(obj)
        result = LLMResponse(
            content=msg.get("content"),
            finish_reason=choice.get("finish_reason") or "",
            usage=obj.get("usage"),
            model=obj.get("model", ""),
            server_tool_calls=list(msg.get("server_tool_calls") or []),
            annotations=list(msg.get("annotations") or []),
            reasoning_content=msg.get("reasoning_content"),
        )
        _record_usage("server_tools", result, latency_ms)
        return result

    @staticmethod
    def _validate_server_tools(tools: List[dict]) -> List[dict]:
        """本地前置校验：把 API 的 400 提前成明确错误（fail fast，不猜）。"""
        if not tools:
            raise LLMError("服务端工具列表为空（未开启任何联网能力）", "protocol")
        out: List[dict] = []
        seen: set = set()
        for t in tools:
            if not isinstance(t, dict) or "type" not in t:
                raise LLMError(f"服务端工具声明非法：{t!r}", "protocol")
            ttype = t["type"]
            if ttype == "function" or "function" in t:
                raise LLMError(
                    "服务端工具不能与本地 function 工具同请求混用（DeepSeek 400）",
                    "protocol",
                )
            if ttype not in _SERVER_TOOL_TYPES:
                raise LLMError(f"未知服务端工具类型：{ttype!r}", "protocol")
            if ttype in seen:
                raise LLMError(f"服务端工具重复声明：{ttype}", "protocol")
            seen.add(ttype)
            out.append({"type": ttype})  # 只保留 type（官方 Chat Completions 形态）
        return out

    # ----- 共用请求层 -----

    def _post_chat(
        self, payload: dict, *, caller: str = "", cancel: "Optional[threading.Event]" = None
    ) -> "tuple[dict, int]":
        """POST /chat/completions：重试 + 取消 + 状态分类 + 200 错误体识别。

        返回 (body, latency_ms)。任何失败都抛 LLMError（两层错误模型）。
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        last_exc: "Optional[LLMError]" = None
        for attempt in range(_MAX_RETRIES + 1):
            if cancel is not None and cancel.is_set():
                raise LLMError("请求已取消", "cancelled")
            started = time.monotonic()
            try:
                with requests.post(
                    self.url, json=payload, headers=headers, timeout=self.timeout
                ) as resp:
                    if resp.status_code != 200:
                        # 脱敏：body 截断 500 字符 + 防御性抹掉回显的 Key
                        body = resp.text[:500].replace(self.api_key, "***")
                        kind = _classify_status(resp.status_code)
                        err = LLMError(f"DeepSeek 返回 {resp.status_code}: {body}",
                                       kind, resp.status_code)
                        if err.retryable and attempt < _MAX_RETRIES:
                            last_exc = err
                            wait = float(2 ** attempt)
                            ra = resp.headers.get("Retry-After")
                            if ra:
                                try:
                                    wait = min(max(wait, float(ra)), _BACKOFF_CAP_S)
                                except ValueError:
                                    pass
                            logger.debug("%s 重试（%d/%d）：%s",
                                         caller or "llm", attempt + 1, _MAX_RETRIES, kind)
                            _interruptible_sleep(wait, cancel)
                            continue
                        raise err
                    try:
                        obj = resp.json()
                    except ValueError as exc:
                        raise LLMError("DeepSeek 返回非 JSON 响应", "protocol") from exc
                # 官方 web_tools 文档：生成中途失败时 HTTP 仍是 200，
                # body 为 {"error": {...}, "usage": {...}}（无 choices）——必须自己查。
                if isinstance(obj, dict) and obj.get("error") and not obj.get("choices"):
                    detail = str(obj["error"])[:300]
                    raise LLMError(f"DeepSeek 生成失败（200 错误体）：{detail}", "server")
                return obj, int((time.monotonic() - started) * 1000)
            except requests.exceptions.Timeout as exc:
                last_exc = LLMError(f"DeepSeek 请求超时: {exc}", "timeout")
            except requests.exceptions.ConnectionError as exc:
                last_exc = LLMError(f"DeepSeek 连接失败: {exc}", "network")
            except requests.exceptions.ChunkedEncodingError as exc:
                last_exc = LLMError(f"DeepSeek 传输中断: {exc}", "network")
            if attempt == _MAX_RETRIES:
                raise last_exc
            _interruptible_sleep(float(2 ** attempt), cancel)
        raise last_exc  # 理论不可达（循环内必 return/raise）


def _extract_choice(obj: dict) -> "tuple[dict, dict]":
    """从响应体取出 (choice, message)，结构异常收敛为 LLMError(protocol)。"""
    try:
        choice = obj["choices"][0]
        return choice, choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"DeepSeek 响应结构异常: {str(obj)[:200]}", "protocol") from exc