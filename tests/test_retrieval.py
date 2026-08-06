"""MemoryRetriever + MemoryManager.retrieve + format_memory_block 单测。

覆盖：五通道混合检索、top_k、空查询、向量缺失降级（权重重归一化）、
无嵌入层时的优雅退化、缓存分数不进提示词。
"""
import pytest

from core.memory import (
    HashingEmbedder,
    MemoryManager,
    SQLiteMemoryStore,
    format_memory_block,
)


@pytest.fixture()
def mgr():
    store = SQLiteMemoryStore(":memory:")
    emb = HashingEmbedder(dim=256)
    m = MemoryManager(store, embedder=emb)
    m.remember("fact", "用户正在准备大学入学考试", importance=8, confidence=4)
    m.remember("preference", "用户喜欢在晚上安静地看书", importance=5, confidence=4)
    m.remember("fact", "用户对猫毛过敏", importance=7, confidence=5)
    m.reindex()
    yield m
    store.close()


def test_retrieve_returns_relevant_hits(mgr):
    hits = mgr.retrieve("用户最近在忙什么学习和考试", top_k=5)
    assert hits, "应当至少命中一条"
    assert any("考试" in h.content for h in hits)


def test_retrieve_respects_top_k(mgr):
    hits = mgr.retrieve("用户 猫 书 考试", top_k=2)
    assert len(hits) <= 2


def test_retrieve_empty_query_returns_empty(mgr):
    assert mgr.retrieve("   ") == []
    assert mgr.retrieve("x", top_k=0) == []


def test_retrieve_no_embedder_degrades_gracefully():
    store = SQLiteMemoryStore(":memory:")
    m = MemoryManager(store)  # retriever=None
    assert m.retrieve("anything") == []
    assert m.memory_block("anything") == ""
    store.close()


def test_format_memory_block_hides_scores(mgr):
    hits = mgr.retrieve("用户 考试", top_k=3)
    block = format_memory_block(hits)
    assert block
    assert "考试" in block
    assert "score=" not in block  # 分数不得进提示词


def test_retrieval_hit_explain_includes_channels(mgr):
    hits = mgr.retrieve("用户 考试", top_k=1)
    assert hits
    exp = hits[0].explain()
    assert "vector=" in exp and "keyword=" in exp


def test_weight_renormalization_when_vector_missing():
    """向量通道不可用（嵌入必败）时，检索降级为关键词/权重模式而非崩溃。"""

    class BoomEmbedder(HashingEmbedder):
        def encode(self, texts):
            raise RuntimeError("no vectors")

    store = SQLiteMemoryStore(":memory:")
    emb = BoomEmbedder(dim=256)
    m = MemoryManager(store, embedder=emb)
    m.remember("fact", "用户准备大学考试", importance=8, confidence=4)
    m.reindex()  # 内部吞掉异常，不抛
    hits = m.retrieve("用户 大学 考试", top_k=3)
    assert isinstance(hits, list)  # 不崩，返回列表
    store.close()
