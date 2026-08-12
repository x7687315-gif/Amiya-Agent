"""Step 2.7 M2.1：Agent 接入 ExtractionEngine 接线 + last_extract_msg_id 书签测试。

不联网：reply 用流式替身，抽取用 chat 替身。覆盖：
T1 自动关零副作用 / T2 自动开入队+书签 / T3 手动不受开关约束 /
T4 extractor=None 安全 / T5 解析降级 / T6 向后兼容 /
T7 窗口含助手话 / T8 完整闭环(抽取→候选→确认→记忆) / T9 书签幂等(窗口仅新轮)。
"""
from __future__ import annotations

import json
from typing import List, Optional

from core.agent import Agent
from core.memory import (
    ExtractionEngine,
    MemoryExtractor,
    MemoryManager,
    SQLiteMemoryStore,
)
from core.persona import load_persona


class FakeReplyLLM:
    """reply 用的流式替身：stream_chat 产出固定回复（reply 不调 chat）。"""

    def stream_chat(self, system, history):
        yield "好的，用户。"

    def chat(self, system, history, *, temperature=None, max_tokens=None):
        return "[]"


class SpyExtractLLM:
    """抽取用的 chat 替身 + 探针：记录调用次数与收到的窗口历史。"""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.chat_calls = 0
        self.last_history = None

    def chat(self, system, history, *, temperature=None, max_tokens=None):
        self.chat_calls += 1
        self.last_history = history  # 即喂给抽取的 role/content 窗口
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)

    def stream_chat(self, system, history):
        yield ""


def _build(*, extract_auto: bool = False, payload=None, with_extractor: bool = True):
    """构造 Agent + MemoryManager + (可选) 抽取引擎。抽取与回复共用同一 memory。"""
    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    spy = SpyExtractLLM(payload if payload is not None else [])
    extractor = ExtractionEngine(MemoryExtractor(mgr), spy) if with_extractor else None
    agent = Agent(
        persona=load_persona(),
        llm=FakeReplyLLM(),
        memory=mgr,
        extractor=extractor,
        extract_auto=extract_auto,
    )
    return agent, mgr, spy


def _run_reply(agent: Agent, text: str) -> None:
    """消费 reply 生成器以跑完整流程（含末尾的 _maybe_extract 钩子）。"""
    list(agent.reply(text))


def test_T1_auto_off_no_side_effect():
    """T1：EXTRACT_AUTO=False 时，reply 绝不调用抽取 LLM、不产生任何候选。"""
    agent, mgr, spy = _build(
        extract_auto=False,
        payload=[{"type": "preference", "content": "用户喜欢猫", "importance": 5}],
    )
    _run_reply(agent, "我养了一只猫")
    assert spy.chat_calls == 0, "EXTRACT_AUTO=False 时绝不应调用抽取 LLM"
    assert mgr.pending_candidates() == [], "不应产生任何候选"


def test_T2_auto_on_enqueues_and_bookmarks():
    """T2：EXTRACT_AUTO=True → 候选入队，且 last_extract_msg_id 书签推进。"""
    agent, mgr, spy = _build(
        extract_auto=True,
        payload=[{"type": "preference", "content": "用户喜欢猫", "importance": 5}],
    )
    _run_reply(agent, "我养了一只猫")
    assert spy.chat_calls == 1
    pending = mgr.pending_candidates()
    assert len(pending) == 1
    assert pending[0]["content"] == "用户喜欢猫"
    assert mgr.state().get("last_extract_msg_id") > 0, "书签应已推进"


def test_T3_manual_ignores_auto_flag():
    """T3：extract_now() 不受 EXTRACT_AUTO 约束（手动整理优先）。"""
    agent, mgr, spy = _build(
        extract_auto=False,
        payload=[{"type": "preference", "content": "用户爱喝咖啡", "importance": 5}],
    )
    _run_reply(agent, "我今天喝了杯咖啡")  # 先产生对话，才有可抽取内容
    ids = agent.extract_now()
    assert spy.chat_calls == 1
    assert len(ids) == 1
    assert mgr.pending_candidates()[0]["content"] == "用户爱喝咖啡"


def test_T4_extractor_none_safe():
    """T4：extractor=None 时，reply 与 extract_now 均不崩、无候选。"""
    agent, mgr, _ = _build(with_extractor=False)
    _run_reply(agent, "你好")
    assert mgr.pending_candidates() == []
    assert agent.extract_now() == []


def test_T5_parse_failure_degrades():
    """T5：抽取 LLM 返回乱码 → 静默降级为无候选，对话不崩。"""
    agent, mgr, spy = _build(extract_auto=True, payload="这不是 json 啦")
    _run_reply(agent, "今天天气不错")
    assert spy.chat_calls == 1
    assert mgr.pending_candidates() == []


def test_T6_backward_compat_positional():
    """T6：旧式位置调用（不带任何抽取参数）行为不变。"""
    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    agent = Agent(load_persona(), FakeReplyLLM(), memory=mgr)  # 纯位置参数
    out = list(agent.reply("你还记得我吗"))
    assert "".join(out) == "好的，用户。"
    assert mgr.pending_candidates() == []


def test_T7_window_includes_assistant():
    """T7：抽取窗口含本轮助手话（reply 末尾才 append，故必须挂末尾）。"""
    agent, mgr, spy = _build(extract_auto=False, payload=[])
    _run_reply(agent, "我周末去爬山")
    agent.extract_now()
    assert spy.last_history is not None
    assert spy.last_history[-1]["role"] == "assistant"
    assert spy.last_history[-1]["content"] == "好的，用户。"


def test_T8_full_closed_loop():
    """T8：完整闭环 抽取 → 候选 → 人工确认 → 进入长期记忆正表。"""
    agent, mgr, spy = _build(
        extract_auto=False,
        payload=[{"type": "preference", "content": "用户偏好先写测试再写实现", "importance": 7}],
    )
    _run_reply(agent, "我习惯先写测试再写实现")
    ids = agent.extract_now()
    assert len(ids) == 1
    cand_id = ids[0]
    # 确认前：候选未进正表
    assert mgr.list_memories() == []
    # 人工确认转正
    mem_id = mgr.confirm_candidate(cand_id)
    assert mem_id > 0
    contents = [m["content"] for m in mgr.list_memories()]
    assert "用户偏好先写测试再写实现" in contents


def test_T9_bookmark_idempotent():
    """T9：书签幂等——第二轮自动抽取只处理「新轮」，窗口不含第一轮。"""
    agent, mgr, spy = _build(
        extract_auto=True,
        payload=[{"type": "preference", "content": "某偏好", "importance": 5}],
    )
    _run_reply(agent, "第一轮话题A")
    spy.chat_calls = 0  # 重置，专注第二轮
    spy.last_history = None
    _run_reply(agent, "第二轮话题B")
    assert spy.chat_calls == 1, "第二轮仍应自动触发"
    assert spy.last_history is not None
    assert len(spy.last_history) == 2, "窗口应只含本轮 2 条，不含第一轮"
    assert spy.last_history[0]["content"] == "第二轮话题B"
