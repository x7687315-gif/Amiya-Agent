"""历史窗口归一化测试（2026-11-23 实机 400 的回归锁）。

事故：`HISTORY_LIMIT=20` → 窗口保留最近 40 条，恰好从某轮的 assistant 开始，
发给 DeepSeek 的消息序列以 assistant 开头 → 400
`an assistant message must follow a user message`。
"""
from __future__ import annotations

import pytest

from core.agent import Agent
from core.persona import load_persona


class _LLM:
    def __init__(self):
        self.seen: list[list[dict]] = []

    def stream_chat(self, system, history):
        self.seen.append(list(history))
        yield "好"


def _agent(limit: int = 2) -> tuple[Agent, _LLM]:
    llm = _LLM()
    return Agent(persona=load_persona(), llm=llm, history_limit=limit), llm


# ---------------------------------------------------------------- _window

def test_window_drops_leading_assistant():
    agent, _ = _agent(limit=2)
    agent._history = [
        {"role": "assistant", "content": "上一轮的尾巴"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    window = agent._window()
    assert window[0]["role"] == "user"
    assert [m["content"] for m in window] == ["u1", "a1"]


def test_window_collapses_consecutive_assistant():
    agent, _ = _agent(limit=4)
    agent._history = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "旧回复"},
        {"role": "assistant", "content": "新回复"},
    ]
    assert [m["content"] for m in agent._window()] == ["u", "新回复"]


def test_window_drops_empty_content():
    agent, _ = _agent(limit=4)
    agent._history = [
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "u2"},
    ]
    assert [m["content"] for m in agent._window()] == ["u", "u2"]


def test_window_always_ends_with_new_user_message():
    """发送那一刻的窗口最后一条必须是本轮用户消息（完成后 history 才会追加回复）。"""
    agent, llm = _agent(limit=1)
    agent._history = [{"role": "assistant", "content": "a"}]

    "".join(agent.reply("新问题"))

    sent = llm.seen[0]
    assert sent[-1]["role"] == "user" and sent[-1]["content"] == "新问题"


# ---------------------------------------------------------------- 端到端载荷

def test_reply_payload_starts_with_user():
    agent, llm = _agent(limit=2)
    agent._history = [
        {"role": "assistant", "content": "上一轮回复"},
        {"role": "user", "content": "老问题"},
        {"role": "assistant", "content": "老回答"},
    ]

    "".join(agent.reply("新问题"))

    sent = llm.seen[0]
    assert sent[0]["role"] == "user"          # 关键：不再是 assistant 开头
    assert sent[-1]["content"] == "新问题"


def test_begin_task_payload_starts_with_user():
    agent, _ = _agent(limit=2)
    agent._history = [
        {"role": "assistant", "content": "上一轮回复"},
        {"role": "user", "content": "老问题"},
        {"role": "assistant", "content": "老回答"},
    ]

    ctx = agent.begin_task("联网问题")

    assert ctx.messages[0]["role"] == "user"
    assert ctx.messages[-1]["content"] == "联网问题"
