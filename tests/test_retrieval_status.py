"""检索状态模型与 Trace 测试（区分"没检索到"与"检索坏了"）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.knowledge.manager import KnowledgeManager
from core.memory import HashingEmbedder, MemoryManager, SQLiteMemoryStore
from core.retrieval_status import RetrievalOutcome, RetrievalStatus
from core.retrieval_trace import RetrievalTrace


class _BoomEmbedder(HashingEmbedder):
    name = "boom"

    def encode(self, texts):
        raise RuntimeError("cannot import name 'is_offline_mode'")


@pytest.fixture()
def store():
    s = SQLiteMemoryStore(":memory:")
    yield s
    s.close()


def _mgr(store, embedder=None):
    return MemoryManager(
        store, embedder=embedder or HashingEmbedder(256),
        min_vector=0.0, min_keyword=0.0,
    )


# ---------------------------------------------------------------- 记忆

def test_empty_store_is_no_relevant_not_failure(store):
    out = _mgr(store).retrieve_outcome("随便问问")
    assert out.status is RetrievalStatus.NO_RELEVANT_RESULTS
    assert out.reason == "no_data"
    assert out.hits == []


def test_relevant_hit_is_success(store):
    m = _mgr(store)
    m.remember("fact", "用户准备大学入学考试", importance=8, confidence=4)
    m.reindex()
    out = m.retrieve_outcome("大学考试")
    assert out.status is RetrievalStatus.SUCCESS
    assert out.hits and out.top_score is not None


def test_broken_embedder_is_degraded_not_empty(store):
    """嵌入坏了但关键词仍可用：必须是 DEGRADED，不能伪装成"没有记忆"。"""
    m = _mgr(store, _BoomEmbedder(256))
    m.remember("fact", "用户准备大学考试", importance=8, confidence=4)
    m.reindex()
    out = m.retrieve_outcome("大学考试")
    assert out.status is RetrievalStatus.DEGRADED
    assert out.reason == "embed_unavailable"


def test_empty_query_reason(store):
    out = _mgr(store).retrieve_outcome("   ")
    assert out.status is RetrievalStatus.NO_RELEVANT_RESULTS
    assert out.reason == "empty_query"


def test_no_embedder_is_failed(store):
    out = MemoryManager(store).retrieve_outcome("任意")
    assert out.status is RetrievalStatus.FAILED
    assert out.reason == "no_embedder"


# ---------------------------------------------------------------- 知识

def _kb(tmp_path: Path) -> Path:
    (tmp_path / "a.md").write_text("# 标题\n正文内容\n", encoding="utf-8")
    return tmp_path


def test_knowledge_success(tmp_path):
    km = KnowledgeManager(_kb(tmp_path), HashingEmbedder(128))
    out = km.retrieve_outcome("标题")
    assert out.status is RetrievalStatus.SUCCESS


def test_knowledge_empty_corpus(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    km = KnowledgeManager(empty, HashingEmbedder(128))
    out = km.retrieve_outcome("标题")
    assert out.status is RetrievalStatus.NO_RELEVANT_RESULTS
    assert out.reason == "no_data"


def test_knowledge_broken_embedder_is_degraded(tmp_path):
    km = KnowledgeManager(_kb(tmp_path), _BoomEmbedder(128))
    out = km.retrieve_outcome("标题")
    assert out.status is RetrievalStatus.DEGRADED
    assert out.reason == "embed_unavailable"


# ---------------------------------------------------------------- Trace

def test_trace_ring_buffer_and_summary():
    t = RetrievalTrace(maxlen=3)
    for _ in range(4):
        t.record("memory", RetrievalOutcome(hits=[1], status=RetrievalStatus.SUCCESS))
    t.record("knowledge", RetrievalOutcome(hits=[], status=RetrievalStatus.DEGRADED,
                                           reason="embed_unavailable"))
    entries = t.snapshot()
    assert len(entries) == 3                      # 环形缓冲上限
    assert t.summary()["knowledge"]["degraded"] == 1
    assert 0 < t.degraded_ratio("knowledge") <= 1


def test_trace_entry_has_no_query_text():
    """trace 只放数字：不得出现 query/正文 字段（隐私红线）。"""
    t = RetrievalTrace()
    t.record("memory", RetrievalOutcome(hits=["很私密的一段记忆"], status=RetrievalStatus.SUCCESS))
    e = t.snapshot()[0]
    assert not hasattr(e, "query")
    assert "很私密" not in repr(e)


# ---------------------------------------------------------------- 上下文接线

def test_context_metadata_carries_status(store):
    from core.context import ContextAssembler
    from core.persona import load_persona
    from core.persona.persona_manager import PersonaManager
    from core.prompt_builder import PromptBuilder

    m = _mgr(store)
    m.remember("fact", "用户准备大学入学考试", importance=8, confidence=4)
    m.reindex()
    persona = load_persona()
    ca = ContextAssembler(
        prompt_builder=PromptBuilder(persona),
        persona_manager=PersonaManager(persona),
        memory=m,
    )
    ctx = ca.assemble("大学考试")
    assert ctx.metadata["retrieval"]["memory"]["status"] == "success"
    assert ctx.metadata["retrieval"]["knowledge"]["status"] == "skipped"
