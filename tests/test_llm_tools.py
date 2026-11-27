"""Phase 1 批1：chat_with_tools 模拟 API 测试（本地 HTTPServer，全离线）。

覆盖（LLM_INTEGRATION.md §8）：tool_calls 单/多解析、非法 arguments、
finish_reason 各值、401/402/400/429/500/网络断、重试与 Retry-After、
cancel 打断退避、Key 脱敏、metrics 记录与失败降级、payload 透传、
Protocol 一致性、旧接口回归（由既有测试套件保证）。
"""
from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

import pytest

import core.llm_client as lc
from core.llm_client import (
    DeepSeekLLMClient,
    LLMClient,
    LLMError,
    LLMResponse,
    ToolCall,
    ToolCapableLLM,
)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def mock_api(responses):
    """responses: [(status, body, headers)] 按请求顺序弹出；记录收到的 payload。

    body 为 dict 时 JSON 序列化；str 原样发送。耗尽后重复最后一个。
    """
    received = []
    queue = list(responses)

    class _H(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(json.loads(self.rfile.read(length) or b"{}"))
            status, body, headers = queue.pop(0) if queue else responses[-1]
            data = json.dumps(body).encode() if isinstance(body, dict) else body.encode()
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    port = _free_port()
    srv = HTTPServer(("127.0.0.1", port), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{port}", received
    finally:
        srv.shutdown()
        srv.server_close()


def _client(url, key="sk-test-SECRET"):
    return DeepSeekLLMClient(api_key=key, base_url=url, model="deepseek-flash", timeout=5)


def _ok(content=None, tool_calls=None, finish="stop", usage=None):
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {
        "choices": [{"message": msg, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        "model": "deepseek-flash",
    }


def _tc(i, name, args):
    return {"index": i, "id": f"call_{i}", "type": "function",
            "function": {"name": name, "arguments": args}}


TOOLS = [{"type": "function", "function": {"name": "get_time", "description": "查时间",
         "parameters": {"type": "object", "properties": {}}}}]
MSGS = [{"role": "user", "content": "现在几点？"}]

# 在 autouse fixture 打桩前捕获真实实现（供取消/退避单元测试用）
_REAL_SLEEP = lc._interruptible_sleep


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch, tmp_path):
    """重试退避置空操作并记录等待秒数（测试不真睡）；metrics 隔离到临时目录。"""
    waits = []
    monkeypatch.setattr(lc, "_interruptible_sleep", lambda s, c: waits.append(s))
    monkeypatch.setattr(lc, "_METRICS_PATH", tmp_path / "metrics.jsonl")
    return waits


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def test_single_tool_call_parsed():
    with mock_api([(200, _ok(tool_calls=[_tc(0, "get_time", '{"tz":"UTC"}')],
                               finish="tool_calls"), {})]) as (url, recv):
        r = _client(url).chat_with_tools("sys", MSGS, tools=TOOLS)
    assert isinstance(r, LLMResponse)
    assert r.finish_reason == "tool_calls"
    assert r.content is None
    assert r.tool_calls == [ToolCall(id="call_0", name="get_time", arguments={"tz": "UTC"})]
    assert r.usage["total_tokens"] == 15
    assert r.model == "deepseek-flash"
    assert recv[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert recv[0]["tools"] == TOOLS and recv[0]["tool_choice"] == "auto"
    assert recv[0]["stream"] is False


def test_multiple_tool_calls_parsed():
    body = _ok(tool_calls=[_tc(0, "a", '{"x":1}'), _tc(1, "b", '{"y":[2,3]}')],
               finish="tool_calls")
    with mock_api([(200, body, {})]) as (url, _):
        r = _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert [t.name for t in r.tool_calls] == ["a", "b"]
    assert r.tool_calls[1].arguments == {"y": [2, 3]}


def test_plain_content_stop():
    with mock_api([(200, _ok(content="你好，用户"), {})]) as (url, _):
        r = _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert r.content == "你好，用户" and r.tool_calls == [] and r.finish_reason == "stop"


def test_unknown_finish_reason_preserved():
    with mock_api([(200, _ok(content="x", finish="brand_new_reason"), {})]) as (url, _):
        assert _client(url).chat_with_tools("s", MSGS, tools=TOOLS).finish_reason == "brand_new_reason"


def test_invalid_arguments_json_raises_protocol():
    with mock_api([(200, _ok(tool_calls=[_tc(0, "a", '{"x":')], finish="tool_calls"), {})]) as (url, _):
        with pytest.raises(LLMError) as ei:
            _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert ei.value.kind == "protocol" and not ei.value.retryable


def test_arguments_not_object_raises_protocol():
    with mock_api([(200, _ok(tool_calls=[_tc(0, "a", "[1,2]")], finish="tool_calls"), {})]) as (url, _):
        with pytest.raises(LLMError) as ei:
            _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert ei.value.kind == "protocol"


def test_missing_choices_raises_protocol():
    with mock_api([(200, {"no_choices": True}, {})]) as (url, _):
        with pytest.raises(LLMError) as ei:
            _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert ei.value.kind == "protocol"


def test_non_json_body_raises_protocol():
    with mock_api([(200, "<html>not json</html>", {})]) as (url, _):
        with pytest.raises(LLMError) as ei:
            _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert ei.value.kind == "protocol"


# ---------------------------------------------------------------------------
# 错误分类与重试
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "auth"), (402, "quota"),
                                         (400, "protocol"), (404, "protocol")])
def test_non_retryable_errors_no_retry(status, kind):
    with mock_api([(status, {"error": {"message": "no"}}, {})]) as (url, recv):
        with pytest.raises(LLMError) as ei:
            _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert ei.value.kind == kind and ei.value.status == status
    assert not ei.value.retryable and len(recv) == 1


def test_429_retries_then_succeeds(_no_wait):
    with mock_api([(429, {"error": "slow down"}, {}), (200, _ok(content="ok"), {})]) as (url, recv):
        r = _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert r.content == "ok" and len(recv) == 2 and _no_wait == [1.0]


def test_429_retry_after_respected(_no_wait):
    with mock_api([(429, {}, {"Retry-After": "3"}), (200, _ok(content="ok"), {})]) as (url, _):
        _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert _no_wait == [3.0]


def test_500_retries_exhausted(_no_wait):
    with mock_api([(500, {}, {})]) as (url, recv):
        with pytest.raises(LLMError) as ei:
            _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert ei.value.kind == "server" and len(recv) == 3 and _no_wait == [1.0, 2.0]


def test_connection_refused_is_network(_no_wait):
    dead = _free_port()  # 绑定后立即释放；Windows 防火墙下可能表现为超时而非拒绝
    c = DeepSeekLLMClient(api_key="k", base_url=f"http://127.0.0.1:{dead}", timeout=2)
    with pytest.raises(LLMError) as ei:
        c.chat_with_tools("s", MSGS, tools=TOOLS)
    # network/timeout 均为可重试传输类错误（平台差异：Win 死端口常挂到超时）
    assert ei.value.kind in ("network", "timeout") and ei.value.retryable


# ---------------------------------------------------------------------------
# 取消
# ---------------------------------------------------------------------------

def test_cancel_before_call_sends_nothing():
    ev = threading.Event()
    ev.set()
    with mock_api([(200, _ok(content="ok"), {})]) as (url, recv):
        with pytest.raises(LLMError) as ei:
            _client(url).chat_with_tools("s", MSGS, tools=TOOLS, cancel=ev)
    assert ei.value.kind == "cancelled" and recv == []


def test_cancel_interrupts_backoff(monkeypatch):
    ev = threading.Event()

    def set_then_sleep(sec, cancel):
        ev.set()
        _REAL_SLEEP(sec, cancel)  # 真实实现检查 Event → 抛 cancelled

    monkeypatch.setattr(lc, "_interruptible_sleep", set_then_sleep)
    with mock_api([(429, {}, {})]) as (url, recv):
        with pytest.raises(LLMError) as ei:
            _client(url).chat_with_tools("s", MSGS, tools=TOOLS, cancel=ev)
    assert ei.value.kind == "cancelled" and len(recv) == 1


def test_interruptible_sleep_unit():
    ev = threading.Event()
    ev.set()
    with pytest.raises(LLMError) as ei:
        _REAL_SLEEP(5.0, ev)
    assert ei.value.kind == "cancelled"
    import time
    t0 = time.monotonic()
    _REAL_SLEEP(0.15, None)
    assert time.monotonic() - t0 >= 0.1


# ---------------------------------------------------------------------------
# 脱敏与 metrics
# ---------------------------------------------------------------------------

def test_api_key_redacted_from_error():
    key = "sk-test-SECRET"
    with mock_api([(401, {"error": {"message": f"invalid key {key}"}}, {})]) as (url, _):
        with pytest.raises(LLMError) as ei:
            _client(url, key=key).chat_with_tools("s", MSGS, tools=TOOLS)
    assert key not in str(ei.value) and "***" in str(ei.value)


def test_metrics_recorded(tmp_path, monkeypatch):
    m = tmp_path / "metrics.jsonl"
    monkeypatch.setattr(lc, "_METRICS_PATH", m)
    usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
             "prompt_cache_hit_tokens": 4,
             "completion_tokens_details": {"reasoning_tokens": 2}}
    with mock_api([(200, _ok(content="ok", usage=usage), {})]) as (url, _):
        _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    entry = json.loads(m.read_text(encoding="utf-8").strip())
    assert entry["caller"] == "chat_with_tools"
    assert entry["reasoning_tokens"] == 2 and entry["cache_hit_tokens"] == 4
    assert entry["model"] == "deepseek-flash" and "latency_ms" in entry


def test_metrics_failure_does_not_break_chat(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(lc, "_METRICS_PATH", blocker / "sub" / "metrics.jsonl")
    with mock_api([(200, _ok(content="ok"), {})]) as (url, _):
        r = _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert r.content == "ok"


# ---------------------------------------------------------------------------
# 透传与协议一致性
# ---------------------------------------------------------------------------

def test_payload_passthrough():
    with mock_api([(200, _ok(content="ok"), {})]) as (url, recv):
        _client(url).chat_with_tools(
            "sys", MSGS, tools=TOOLS, tool_choice="none",
            temperature=0.1, max_tokens=77, thinking={"type": "disabled"})
    p = recv[0]
    assert p["tool_choice"] == "none" and p["temperature"] == 0.1
    assert p["max_tokens"] == 77 and p["thinking"] == {"type": "disabled"}


def test_thinking_omitted_by_default():
    with mock_api([(200, _ok(content="ok"), {})]) as (url, recv):
        _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert "thinking" not in recv[0]


def test_tools_none_omits_tool_fields():
    with mock_api([(200, _ok(content='{"a":1}'), {})]) as (url, recv):
        r = _client(url).chat_with_tools("s", MSGS, response_format={"type": "json_object"})
    assert "tools" not in recv[0] and "tool_choice" not in recv[0]
    assert recv[0]["response_format"] == {"type": "json_object"}
    assert r.content == '{"a":1}'


def test_response_format_omitted_by_default():
    with mock_api([(200, _ok(content="ok"), {})]) as (url, recv):
        _client(url).chat_with_tools("s", MSGS, tools=TOOLS)
    assert "response_format" not in recv[0]


def test_protocol_conformance():
    c = DeepSeekLLMClient(api_key="k", base_url="http://127.0.0.1:1")
    assert isinstance(c, LLMClient)
    assert isinstance(c, ToolCapableLLM)

# ---------------------------------------------------------------------------
# 2.x 模型迁移：实例级 thinking 注入（裁决 C，单独提交可回滚）
# ---------------------------------------------------------------------------

def test_constructor_thinking_injected_into_fc_payload():
    with mock_api([(200, _ok(content="ok"), {})]) as (url, recv):
        c = DeepSeekLLMClient(api_key="k", base_url=url, thinking={"type": "disabled"})
        c.chat_with_tools("s", MSGS, tools=TOOLS)
    assert recv[0]["thinking"] == {"type": "disabled"}


def test_explicit_thinking_overrides_constructor():
    with mock_api([(200, _ok(content="ok"), {})]) as (url, recv):
        c = DeepSeekLLMClient(api_key="k", base_url=url, thinking={"type": "disabled"})
        c.chat_with_tools("s", MSGS, tools=TOOLS, thinking={"type": "enabled"})
    assert recv[0]["thinking"] == {"type": "enabled"}


def test_constructor_thinking_injected_into_legacy_chat():
    with mock_api([(200, _ok(content="ok"), {})]) as (url, recv):
        c = DeepSeekLLMClient(api_key="k", base_url=url, thinking={"type": "disabled"})
        assert c.chat("s", MSGS) == "ok"
    assert recv[0]["thinking"] == {"type": "disabled"}


def test_constructor_thinking_injected_into_stream():
    sse = 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    with mock_api([(200, sse, {})]) as (url, recv):
        c = DeepSeekLLMClient(api_key="k", base_url=url, thinking={"type": "disabled"})
        assert "".join(c.stream_chat("s", MSGS)) == "hi"
    assert recv[0]["thinking"] == {"type": "disabled"}


def test_no_thinking_field_when_unset():
    with mock_api([(200, _ok(content="ok"), {})]) as (url, recv):
        DeepSeekLLMClient(api_key="k", base_url=url).chat("s", MSGS)
    assert "thinking" not in recv[0]
