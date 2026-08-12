"""Step 2.7 M1：ExtractionEngine + 候选流程测试。

不联网：用 FakeLLM（实现 LLMClient.chat 返回固定 JSON）驱动"LLM→解析→入队"全链路。
覆盖：T1 抽取入队 / T4 解析失败降级 / T5 临时不抽 / T8 去重 / T9 黑名单 + 三柱隔离导入守卫。
"""
from __future__ import annotations

import json
from typing import Dict, List

from core.memory import (
    ExtractionEngine,
    MemoryExtractor,
    MemoryManager,
    SQLiteMemoryStore,
)


class FakeLLM:
    """LLM 测试替身：chat() 返回预设 payload（str 或 list），不联网。

    为什么需要：M1 不依赖真实 DeepSeek。用可预测替身构造
    「正常 JSON / 乱码 / 临时信息([])」三类输入，使抽取链路可重复验证。
    """

    def __init__(self, payload) -> None:
        self.payload = payload  # str 原样返回；list 经 json.dumps 返回

    def chat(self, system, history, *, temperature=None, max_tokens=None) -> str:
        if isinstance(self.payload, str):
            return self.payload
        return json.dumps(self.payload)

    def stream_chat(self, system, history):
        yield ""


def _engine(payload) -> "tuple[ExtractionEngine, MemoryManager]":
    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    extractor = MemoryExtractor(mgr)
    return ExtractionEngine(extractor, FakeLLM(payload)), mgr


def test_T1_extract_enqueues_candidate():
    """T1：正常 JSON → 候选落 memory_candidate，pending 可见。"""
    payload = [
        {
            "type": "preference",
            "content": "用户偏好先写测试再写实现",
            "importance": 7,
            "confidence": 4,
            "reason": "反复提到",
        }
    ]
    engine, mgr = _engine(payload)
    window = [{"role": "user", "content": "我一般先写测试"}]
    ids = engine.extract(window)
    assert ids == [1], ids
    pending = mgr.pending_candidates()
    assert len(pending) == 1
    assert pending[0]["content"] == "用户偏好先写测试再写实现"
    assert pending[0]["type"] == "preference"


def test_T4_garbage_json_silent_empty():
    """T4：LLM 返回乱码 → 静默降级为 []，绝不抛异常。"""
    engine, _ = _engine("这根本不是 json 啦")
    ids = engine.extract([{"role": "user", "content": "随便说点"}])
    assert ids == []


def test_T4b_llm_raises_silent_empty():
    """T4 变体：LLM 调用抛异常 → 静默降级为 []。"""

    class BoomLLM:
        def chat(self, system, history, *, temperature=None, max_tokens=None):
            raise RuntimeError("网络挂了")

        def stream_chat(self, system, history):
            yield ""

    store = SQLiteMemoryStore(":memory:")
    engine = ExtractionEngine(MemoryExtractor(MemoryManager(store)), BoomLLM())
    assert engine.extract([{"role": "user", "content": "x"}]) == []


def test_T5_temporary_info_no_candidate():
    """T5：临时信息（prompt 规则使模型输出 []）→ 无候选。"""
    engine, _ = _engine("[]")
    ids = engine.extract([{"role": "user", "content": "今天天气不错"}])
    assert ids == []


def test_T8_dedup_same_content_returns_same_id():
    """T8：同 (type,content) 抽两次 → 第二次返回已有 id，权重加权。"""
    payload = [{"type": "preference", "content": "用户周末常去爬山", "importance": 5}]
    engine, mgr = _engine(payload)
    window = [{"role": "user", "content": "我周末去爬山"}]
    ids1 = engine.extract(window)
    ids2 = engine.extract(window)
    assert ids1 == ids2 == [1]  # 同一 id
    # 重复提交应加权：importance 不低于首次设定
    pending = mgr.pending_candidates()
    assert pending[0]["importance"] >= 5


def test_T9_blacklist_blocks_candidate():
    """T9：content 命中黑名单 → propose 返回 0，候选不入队。"""
    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    store.add_blacklist("秘密")  # 黑名单由 store 层维护；propose 经 is_blacklisted 拦截
    engine = ExtractionEngine(
        MemoryExtractor(mgr),
        FakeLLM([{"type": "fact", "content": "用户的秘密是 X", "importance": 3}]),
    )
    ids = engine.extract([{"role": "user", "content": "我的秘密是 X"}])
    assert 0 in ids
    assert mgr.pending_candidates() == []


def test_T6_lite_three_pillar_isolation_import_guard():
    """T6-lite：ExtractionEngine 运行时只依赖 core.memory，不 import knowledge/persona。

    保证抽取引擎不会越界触碰 Knowledge 柱（knowledge/*.md）或 Persona 柱
    （config/persona/*、persona_state），三柱隔离由依赖收敛保证。
    """
    import pathlib

    src = pathlib.Path("core/memory/extraction_engine.py").read_text(encoding="utf-8")
    import_lines = [
        ln for ln in src.splitlines() if ln.strip().startswith(("import ", "from "))
    ]
    forbidden = ("knowledge", "persona")
    for ln in import_lines:
        for f in forbidden:
            assert f not in ln, f"抽取引擎不应 import {f}：{ln}"
    # 必须依赖 core.memory（ExtractedMemory / MemoryExtractor）
    assert any("core.memory" in ln or "from .extractor" in ln or "from .store" in ln
               for ln in import_lines), "抽取引擎应依赖 core.memory"
