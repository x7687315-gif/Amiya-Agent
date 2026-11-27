"""Phase 4A 测试：DeepSeek 服务端工具协议层（本地 HTTPServer，全离线）。

事实依据：https://api-docs.deepseek.com/guides/web_tools（2026-11-23 核对）
- tools 只写 type，无 name；不能与 function 工具混用；tool_choice 不发送
- 响应在 message.server_tool_calls / annotations / reasoning_content
- 生成中途失败可能仍是 HTTP 200 + {"error": ...}

覆盖：声明生成、payload 形状、响应解析、五类本地前置校验、200 错误体、
重试复用、cancel、以及「服务端工具 ≠ 本地 ToolCall」的隔离断言。
"""
from __future__ import annotations

import json
import socket
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import core.llm_client as lc
from core.llm_client import (
    DeepSeekLLMClient,
    LLMError,
    SERVER_TOOL_READ_URL,
    SERVER_TOOL_WEB_SEARCH,
    server_tool_declarations,
)


# ---------------------------------------------------------------- 测试基建

def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def mock_api(responses):
    """responses: [(status, body, headers)]，按请求顺序弹出；记录收到的 payload。"""
    received: list = []
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


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch, tmp_path):
    waits: list = []
    monkeypatch.setattr(lc, "_interruptible_sleep", lambda s, c: waits.append(s))
    monkeypatch.setattr(lc, "_METRICS_PATH", tmp_path / "metrics.jsonl")
    return waits


MSGS = [{"role": "user", "content": "今天上海天气怎么样？"}]

WEB_OK = {
    "choices": [{
        "message": {
            "role": "assistant",
            "reasoning_content": "需要查天气。",
            "content": "今天上海多云。",
            "annotations": [{
                "type": "url_citation",
                "url_citation": {"url": "https://example.com/weather",
                                 "title": "天气", "start_index": 0, "end_index": 6},
            }],
            "server_tool_calls": [{
                "index": 0,
                "reasoning_content": "",
                "calls": [{
                    "index": 0, "id": "srvtoolu_s_01",
                    "type": "deepseek_web_search", "kind": "search",
                    "action": {"type": "search", "queries": ["上海天气"]},
                    "status": "completed",
                    "sources": [{"type": "url", "url": "https://example.com/weather",
                                 "title": "天气", "snippet": "多云", "site_name": "示例"}],
                }],
            }],
        },
        "finish_reason": "stop",
    }],
    "usage": {"prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60,
              "server_tool_use": {"tool_search": 1, "tool_fetch": 0}},
    "model": "deepseek-flash",
}


# ---------------------------------------------------------------- 声明生成

def test_declarations_by_switch():
    assert server_tool_declarations(web_search=True, read_url=True) == [
        {"type": SERVER_TOOL_WEB_SEARCH}, {"type": SERVER_TOOL_READ_URL}]
    assert server_tool_declarations(web_search=True, read_url=False) == [
        {"type": SERVER_TOOL_WEB_SEARCH}]
    assert server_tool_declarations(web_search=False, read_url=True) == [
        {"type": SERVER_TOOL_READ_URL}]
    assert server_tool_declarations(web_search=False, read_url=False) == []


def test_declarations_have_no_name_or_function():
    """官方 Chat Completions 形态只写 type；带 name/function 会被 400。"""
    for decl in server_tool_declarations(web_search=True, read_url=True):
        assert set(decl.keys()) == {"type"}


# ---------------------------------------------------------------- 请求形状

def test_payload_shape_and_response_parsing():
    with mock_api([(200, WEB_OK, {})]) as (url, received):
        client = _client(url)
        resp = client.chat_with_server_tools(
            "SYS", MSGS, tools=server_tool_declarations(web_search=True, read_url=False))

    payload = received[0]
    assert payload["tools"] == [{"type": "deepseek_web_search"}]
    assert "tool_choice" not in payload          # 官方：指名会 400
    assert payload["stream"] is False
    assert payload["messages"][0] == {"role": "system", "content": "SYS"}

    assert resp.content == "今天上海多云。"
    assert resp.finish_reason == "stop"
    assert resp.reasoning_content == "需要查天气。"
    assert resp.server_tool_calls[0]["calls"][0]["action"]["queries"] == ["上海天气"]
    assert resp.annotations[0]["url_citation"]["url"] == "https://example.com/weather"
    # 红线：服务端工具绝不解析成本地 ToolCall（进不了 Executor）
    assert resp.tool_calls == []


def test_thinking_passthrough():
    with mock_api([(200, WEB_OK, {})]) as (url, received):
        _client(url).chat_with_server_tools(
            "SYS", MSGS, tools=[{"type": SERVER_TOOL_WEB_SEARCH}],
            thinking={"type": "enabled"})
    assert received[0]["thinking"] == {"type": "enabled"}


# ---------------------------------------------------------------- 本地前置校验

def test_rejects_empty_tools():
    with pytest.raises(LLMError) as e:
        _client("http://127.0.0.1:1").chat_with_server_tools("S", MSGS, tools=[])
    assert e.value.kind == "protocol"


def test_rejects_mixing_function_tools():
    fn_tool = [{"type": "function", "function": {"name": "get_time"}}]
    with pytest.raises(LLMError) as e:
        _client("http://127.0.0.1:1").chat_with_server_tools("S", MSGS, tools=fn_tool)
    assert e.value.kind == "protocol"
    assert "混用" in str(e.value) or "function" in str(e.value)


def test_rejects_unknown_and_duplicate():
    client = _client("http://127.0.0.1:1")
    with pytest.raises(LLMError):
        client.chat_with_server_tools("S", MSGS, tools=[{"type": "deepseek_unknown"}])
    with pytest.raises(LLMError):
        client.chat_with_server_tools(
            "S", MSGS,
            tools=[{"type": SERVER_TOOL_WEB_SEARCH}, {"type": SERVER_TOOL_WEB_SEARCH}])
    with pytest.raises(LLMError):
        client.chat_with_server_tools("S", MSGS, tools=[{"name": "x"}])


# ---------------------------------------------------------------- 错误路径

def test_http_400_is_protocol_error():
    # 400 不可重试，直接抛（不消耗重试预算）
    with mock_api([(400, {"error": {"message": "bad tools"}}, {})]) as (url, received):
        with pytest.raises(LLMError) as e:
            _client(url).chat_with_server_tools(
                "S", MSGS, tools=[{"type": SERVER_TOOL_WEB_SEARCH}])
    assert e.value.kind == "protocol" and e.value.status == 400
    assert len(received) == 1


def test_200_error_body_is_server_error():
    """官方：生成中途失败可能 HTTP 200 + error 体（无 choices）。"""
    body = {"error": {"code": "server_error", "message": "推理中断"},
            "usage": {"prompt_tokens": 10, "completion_tokens": 0}}
    with mock_api([(200, body, {})]) as (url, _):
        with pytest.raises(LLMError) as e:
            _client(url).chat_with_server_tools(
                "S", MSGS, tools=[{"type": SERVER_TOOL_WEB_SEARCH}])
    assert e.value.kind in ("server", "protocol")
    assert "推理中断" in str(e.value)


def test_500_retries_then_succeeds():
    with mock_api([(500, {"error": "boom"}, {}), (200, WEB_OK, {})]) as (url, received):
        resp = _client(url).chat_with_server_tools(
            "S", MSGS, tools=[{"type": SERVER_TOOL_WEB_SEARCH}])
    assert resp.content == "今天上海多云。"
    assert len(received) == 2


def test_cancel_before_request():
    import threading as _t

    ev = _t.Event()
    ev.set()
    with pytest.raises(LLMError) as e:
        _client("http://127.0.0.1:1").chat_with_server_tools(
            "S", MSGS, tools=[{"type": SERVER_TOOL_WEB_SEARCH}], cancel=ev)
    assert e.value.kind == "cancelled"


def test_api_key_not_in_error_message():
    body = {"error": {"message": "bad key sk-test-SECRET"}}
    with mock_api([(401, body, {})]) as (url, _):
        with pytest.raises(LLMError) as e:
            _client(url).chat_with_server_tools(
                "S", MSGS, tools=[{"type": SERVER_TOOL_WEB_SEARCH}])
    assert "sk-test-SECRET" not in str(e.value)
