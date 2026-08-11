"""Step A · Memory Gate 数据隔离测试。

验证自动抽取的记忆只能经候选闸门进入长期记忆：
1. 临时信息经抽取器提议后不进入长期记忆、retrieve 读不到；
2. 稳定偏好可经抽取器进入候选（pending_candidates 可见）；
3. reject 后不污染 retrieve / long_term，且同内容不再被打扰；
4. confirm 后候选转正且能被 retrieve 命中；
5. 分层守卫：抽取器源码不得包含任何直写长期记忆正表的调用。

retrieve 需要嵌入层才返回结果，故本文件用纯 stdlib 的 HashingEmbedder
（不依赖 sentence-transformers），让检索链路在测试环境完整可用。
"""
import inspect
import pathlib

import pytest

from core.memory import (
    ExtractedMemory,
    HashingEmbedder,
    MemoryExtractor,
    MemoryManager,
    SQLiteMemoryStore,
)


@pytest.fixture()
def store():
    s = SQLiteMemoryStore(":memory:")
    yield s
    s.close()


@pytest.fixture()
def gate(store):
    """带哈希嵌入层的 MemoryManager，retrieve 可用。"""
    emb = HashingEmbedder(dim=256)
    m = MemoryManager(store, embedder=emb)
    return m


# ---------- 1. 临时信息不进入长期记忆 ----------


def test_temporary_info_does_not_enter_long_term(gate):
    ext = MemoryExtractor(gate)
    cid = ext.propose_one(
        ExtractedMemory("fact", "用户今天中午吃了牛肉面", importance=3)
    )
    assert cid > 0  # 已进入候选闸门
    # 关键：未经人工确认，绝不进 memory 正表
    assert gate.list_memories() == []
    # 检索只扫 memory 正表，候选不在其列 → 读不到
    hits = gate.retrieve("用户中午吃了什么", top_k=5)
    assert not any("牛肉面" in h.content for h in hits)


# ---------- 2. 稳定偏好可进入候选 ----------


def test_stable_preference_can_enter_candidate(gate):
    ext = MemoryExtractor(gate)
    cid = ext.propose_one(
        ExtractedMemory(
            "preference", "用户喜欢机械键盘", importance=6, reason="提过两次"
        )
    )
    assert cid > 0
    pending = gate.pending_candidates()
    assert len(pending) == 1
    assert pending[0]["content"] == "用户喜欢机械键盘"
    assert pending[0]["type"] == "preference"
    assert pending[0]["reason"] == "提过两次"


# ---------- 3. reject 不污染 retrieve / long_term ----------


def test_reject_does_not_pollute_retrieve(gate):
    # 先放一条已确认记忆，让 retrieve 链路真正有命中（证明检索是通的）
    gate.remember("fact", "用户在准备读书计划", importance=5)
    ext = MemoryExtractor(gate)
    cid = ext.propose_one(ExtractedMemory("preference", "用户喜欢晴天"))
    assert gate.reject_candidate(cid) is True

    hits = gate.retrieve("用户喜欢什么天气 读书计划", top_k=5)
    contents = [h.content for h in hits]
    assert "用户在准备读书计划" in contents  # 已确认记忆正常召回
    assert not any("晴天" in c for c in contents)  # 被拒候选永不污染检索
    # 被拒候选也不进长期记忆（已确认记忆"用户在准备读书计划"理应还在）
    assert not any("晴天" in m["content"] for m in gate.list_memories())


def test_rejected_content_never_resurfaces(gate):
    ext = MemoryExtractor(gate)
    cid = ext.propose_one(ExtractedMemory("preference", "用户喜欢晴天"))
    gate.reject_candidate(cid)
    # 同一内容再次被抽取时，应被闸门拦截而非重新打扰
    assert ext.propose_one(ExtractedMemory("preference", "用户喜欢晴天")) == 0
    assert gate.pending_candidates() == []


# ---------- 4. confirm 后转正且可被检索 ----------


def test_confirm_promotes_and_becomes_retrievable(gate):
    ext = MemoryExtractor(gate)
    cid = ext.propose_one(
        ExtractedMemory("fact", "用户对猫毛过敏", importance=7, confidence=5)
    )
    mid = gate.confirm_candidate(cid)
    assert mid > 0
    assert gate.list_memories()[0]["content"] == "用户对猫毛过敏"
    hits = gate.retrieve("用户对什么过敏", top_k=5)
    assert any("猫毛" in h.content for h in hits)


# ---------- 5. 分层守卫：抽取器不得直写长期记忆 ----------


def test_extractor_module_never_writes_long_term():
    """抽取器源码不得出现任何直写 memory 正表的调用，物理隔离由构造保证。"""
    src = pathlib.Path(inspect.getfile(MemoryExtractor)).read_text(encoding="utf-8")
    assert "remember(" not in src
    assert "upsert_memory(" not in src
    assert ".remember(" not in src
    # 类本身也不暴露直写长期记忆的方法
    assert not hasattr(MemoryExtractor, "remember")
    assert not hasattr(MemoryExtractor, "upsert_memory")


def test_extractor_accepts_only_valid_types():
    store = SQLiteMemoryStore(":memory:")
    ext = MemoryExtractor(MemoryManager(store, embedder=HashingEmbedder(dim=256)))
    with pytest.raises(ValueError):
        ext.propose_one(ExtractedMemory("nonsense", "内容"))
    store.close()
