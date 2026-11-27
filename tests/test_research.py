"""Phase 4B 测试：ResearchRunner + ResearchCard + 联网模式互斥（全离线）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.application.research import (
    ResearchCall,
    ResearchResult,
    ResearchRunner,
    ResearchSource,
    extract_calls,
    extract_sources,
)
from core.context import AssembledContext
from core.llm_client import LLMError, LLMResponse
from ui.app import AmiyaApp
from ui.components.input_bar import InputBar
from ui.components.research_card import ResearchCard


# ---------------------------------------------------------------- 纯函数

def _resp(**kw) -> LLMResponse:
    return LLMResponse(content=kw.pop("content", "答"), **kw)


def test_extract_sources_from_calls_and_pages():
    resp = _resp(server_tool_calls=[
        {"index": 0, "calls": [
            {"index": 0, "type": "deepseek_web_search", "kind": "search",
             "action": {"type": "search", "queries": ["x"]},
             "status": "completed",
             "sources": [{"url": "https://a.com", "title": "A", "snippet": "s1"},
                         {"url": "https://b.com", "title": "B"}]},
        ]},
        {"index": 1, "calls": [
            {"index": 0, "type": "deepseek_read_url", "kind": "read_link",
             "action": {"type": "read_link", "url": "https://c.com"},
             "status": "completed", "page": {"url": "https://c.com", "title": "C"}},
        ]},
    ])
    sources = extract_sources(resp)
    assert [s.url for s in sources] == ["https://a.com", "https://b.com", "https://c.com"]
    assert sources[0].title == "A" and sources[0].snippet == "s1"


def test_extract_sources_dedupes_and_limits():
    resp = _resp(server_tool_calls=[{"index": 0, "calls": [
        {"sources": [{"url": "https://a.com"}, {"url": "https://a.com"}]}]}])
    assert len(extract_sources(resp)) == 1

    many = [{"index": 0, "calls": [
        {"sources": [{"url": f"https://{i}.com"} for i in range(20)]}]}]
    assert len(extract_sources(_resp(server_tool_calls=many), limit=5)) == 5


def test_extract_sources_falls_back_to_annotations():
    resp = _resp(annotations=[
        {"type": "url_citation", "url_citation": {"url": "https://n.com", "title": "N"}}])
    assert [s.url for s in extract_sources(resp)] == ["https://n.com"]


def test_extract_calls_labels():
    resp = _resp(server_tool_calls=[
        {"index": 0, "calls": [
            {"kind": "search", "action": {"type": "search", "queries": ["上海天气"]},
             "status": "completed", "sources": [{"url": "u1"}, {"url": "u2"}]},
            {"kind": "read_link", "action": {"type": "read_link", "url": "https://x.com"},
             "status": "completed", "page": {"url": "https://x.com", "title": "标题"}},
            {"kind": "search", "action": {"type": "search", "queries": ["失败的"]},
             "status": "failed", "error": {"code": "unavailable"}},
        ]},
    ])
    rows = extract_calls(resp)
    assert rows[0].label == "搜索：上海天气" and rows[0].detail == "2 条结果"
    assert rows[1].label == "读取：https://x.com" and rows[1].ok is True
    assert rows[2].ok is False and rows[2].detail == "unavailable"


# ---------------------------------------------------------------- Runner

class _FakeAgent:
    def __init__(self):
        self.begun: list[str] = []
        self.finished: list[tuple] = []

    def begin_task(self, text):
        self.begun.append(text)
        return AssembledContext(system_prompt="SYS",
                                messages=[{"role": "user", "content": text}],
                                emotion="happy")

    def finish_task(self, text, *, emotion="calm"):
        self.finished.append((text, emotion))


class _FakeLLM:
    def __init__(self, resp=None, exc=None):
        self.resp = resp
        self.exc = exc
        self.calls: list[dict] = []

    def chat_with_server_tools(self, system, messages, *, tools, **kw):
        self.calls.append({"system": system, "messages": messages, "tools": tools})
        if self.exc is not None:
            raise self.exc
        return self.resp


WEB_MSG = {
    "role": "assistant",
    "content": "今天上海多云。",
    "server_tool_calls": [{"index": 0, "calls": [
        {"kind": "search", "action": {"type": "search", "queries": ["上海天气"]},
         "status": "completed", "sources": [{"url": "https://w.com", "title": "天气"}]}]}],
    "annotations": [],
}


def test_runner_success_persists_and_returns_sources():
    agent = _FakeAgent()
    llm = _FakeLLM(LLMResponse(content="今天上海多云。", finish_reason="stop",
                               server_tool_calls=WEB_MSG["server_tool_calls"]))
    runner = ResearchRunner(agent=agent, llm=llm,
                            tools=[{"type": "deepseek_web_search"}])

    result = runner.run("上海天气？")

    assert result.ok and result.text == "今天上海多云。"
    assert [s.url for s in result.sources] == ["https://w.com"]
    assert result.calls and result.calls[0].label == "搜索：上海天气"
    assert agent.begun == ["上海天气？"]
    assert agent.finished == [("今天上海多云。", "happy")]  # 情绪沿用本轮判定
    assert llm.calls[0]["tools"] == [{"type": "deepseek_web_search"}]
    assert llm.calls[0]["system"].startswith("SYS")  # 人格提示在，规则追加在其后
    assert runner.is_running is False


def test_runner_llm_error_is_contained():
    agent = _FakeAgent()
    llm = _FakeLLM(exc=LLMError("401 bad key", "auth", 401))
    runner = ResearchRunner(agent=agent, llm=llm, tools=[{"type": "deepseek_web_search"}])

    result = runner.run("hi")

    assert result.ok is False and result.error_kind == "auth"
    assert agent.finished == []  # 失败不落 assistant 消息
    assert runner.is_running is False


def test_runner_content_filter():
    agent = _FakeAgent()
    llm = _FakeLLM(LLMResponse(content=None, finish_reason="content_filter"))
    runner = ResearchRunner(agent=agent, llm=llm, tools=[{"type": "deepseek_web_search"}])
    result = runner.run("敏感问题")
    assert result.ok is False and result.error_kind == "content_filter"
    assert agent.finished == []


def test_runner_empty_answer():
    agent = _FakeAgent()
    llm = _FakeLLM(LLMResponse(content="   ", finish_reason="stop"))
    runner = ResearchRunner(agent=agent, llm=llm, tools=[{"type": "deepseek_web_search"}])
    result = runner.run("hi")
    assert result.ok is False and result.error_kind == "empty"
    assert agent.finished == []


def test_runner_single_flight_and_no_cancel():
    runner = ResearchRunner(agent=_FakeAgent(), llm=_FakeLLM(), tools=[{"type": "x"}])
    runner._running = True
    assert runner.run("hi").error_kind == "busy"
    runner._running = False
    assert runner.cancel() is False  # 非流式无法在途打断，明确不支持


def test_runner_requires_tools():
    with pytest.raises(ValueError):
        ResearchRunner(agent=_FakeAgent(), llm=_FakeLLM(), tools=[])


# ---------------------------------------------------------------- UI

def test_research_card_complete_and_fail():
    card = ResearchCard(available_width=600)
    assert card._status.value == "正在检索…"
    card.complete(ResearchResult(
        True, text="答",
        sources=[ResearchSource("https://a.com", "A")],
        calls=[ResearchCall("search", "搜索：x", True, "1 条结果")],
    ))
    assert card._status.value == "已完成"
    assert card._sources_title.visible is True
    assert len(card._sources.controls) == 1
    # 幂等：终态后不会再被改成失败
    card.fail("boom")
    assert card._status.value == "已完成"


def test_input_bar_modes_are_mutually_exclusive():
    bar = InputBar(on_send=lambda s: None, task_mode_available=True, web_mode_available=True)
    bar.set_task_mode(True)
    assert bar.task_mode is True and bar.web_mode is False
    bar.set_web_mode(True)
    assert bar.web_mode is True and bar.task_mode is False
    assert "联网" in bar._web_btn.tooltip
    bar.set_web_mode(False)
    assert bar._field.hint_text == "想和助手聊点什么？"


def test_input_bar_web_unavailable():
    """能力未开启时按钮仍可见（灰态），点击给出指引而非静默失效。"""
    hints: list[str] = []
    bar = InputBar(on_send=lambda s: None, web_mode_available=False,
                   on_unavailable=hints.append)
    assert bar._web_btn.visible is not False
    assert "WEB_TOOLS_ENABLED=1" in bar._web_btn.tooltip

    bar.set_web_mode(True)
    assert bar.web_mode is False

    bar._toggle_web_mode(None)
    assert hints and "WEB_TOOLS_ENABLED=1" in hints[0]


def test_use_web_path_requires_capability_and_toggle():
    app = AmiyaApp()
    app.input_bar = InputBar(on_send=lambda s: None, web_mode_available=True)
    assert app._use_web_path() is False
    app._research_runner = object()
    assert app._use_web_path() is False
    app.input_bar.set_web_mode(True)
    assert app._use_web_path() is True
    app._research_runner = None
    assert app._use_web_path() is False


# ---------------------------------------------------------------- 错误文案

def test_friendly_error_maps_known_kinds():
    from core.application.research import friendly_error

    assert "API Key" in friendly_error("auth", "raw json...")
    assert "余额" in friendly_error("quota")
    assert "服务端拒绝" in friendly_error("protocol", 'DeepSeek 返回 400: {"error":...}')
    # 未知 kind 退回截断原文，绝不空着
    assert friendly_error("", "some detail") == "some detail"
    assert len(friendly_error("weird", "x" * 200)) <= 80


# ---------------------------------------------------------------- 人格 vs 联网规则

def test_runner_appends_research_rules():
    """人格提示会让模型回避联网（实测 0 次调用）；规则必须拼在 system 末尾。"""
    agent = _FakeAgent()
    llm = _FakeLLM(LLMResponse(content="答", finish_reason="stop"))
    runner = ResearchRunner(agent=agent, llm=llm, tools=[{"type": "deepseek_web_search"}])

    runner.run("明天湛江天气")

    system = llm.calls[0]["system"]
    assert "SYS" in system                              # 人格提示仍在
    assert "【本轮为联网检索模式】" in system            # 追加了检索规则
    assert system.index("SYS") < system.index("【本轮为联网检索模式】")


def test_card_flags_when_model_did_not_call_search():
    from core.application.research import ResearchResult

    card = ResearchCard()
    card.complete(ResearchResult(True, text="助手不知道呢"))
    assert "未调用" in card._status.value

    card2 = ResearchCard()
    card2.complete(ResearchResult(
        True, text="有搜索结果",
        calls=[ResearchCall("search", "搜索：x", True, "3 条结果")]))
    assert card2._status.value == "已完成"
