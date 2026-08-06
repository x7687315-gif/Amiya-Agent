"""SQLiteMemoryStore 单元测试（Phase 2-A Step 2.1）。

全部使用 ":memory:"，无外部依赖。覆盖：协议实现、对话读写、记忆幂等 upsert、
importance 持久化与排序、events/goals 形状、用户画像、persona_state、向量读写、
memory_control（黑名单增/判/删）、跨天陪伴时长重置。
"""
from __future__ import annotations

import pytest

from core.memory import SQLiteMemoryStore
from core.memory.store import MemoryStore


def _store() -> SQLiteMemoryStore:
    return SQLiteMemoryStore(":memory:")


def test_implements_protocol():
    assert isinstance(_store(), MemoryStore)


def test_add_and_recent_messages():
    s = _store()
    s.add_message("user", "你好", "sess1")
    s.add_message("assistant", "你好呀", "sess1")
    recent = s.recent_messages(limit=10)
    assert [m["role"] for m in recent] == ["user", "assistant"]
    assert recent[0]["content"] == "你好"


def test_messages_since():
    s = _store()
    ids = [s.add_message("user", f"m{i}", "s") for i in range(3)]
    since = s.messages_since(ids[0])
    assert len(since) == 2
    assert since[0]["content"] == "m1"


def test_upsert_memory_idempotent():
    s = _store()
    s.upsert_memory("fact", "喜欢猫", confidence=4, importance=5)
    s.upsert_memory("fact", "喜欢猫", confidence=5, importance=8)  # 同 type+content
    rows = s.memories(types=("fact",))
    assert len(rows) == 1
    assert rows[0]["confidence"] == 5
    assert rows[0]["importance"] == 8


def test_importance_persisted_and_ranked():
    s = _store()
    s.upsert_memory("goal", "考研", importance=9)
    s.upsert_memory("fact", "喜欢猫", importance=3)
    rows = s.memories(limit=10)
    assert rows[0]["content"] == "考研"
    assert rows[0]["importance"] == 9


def test_events_and_goals_shapes():
    s = _store()
    s.upsert_memory("event", "完成架构", importance=4)
    s.upsert_memory("goal", "做AI Agent", importance=6)
    ev = s.events(limit=5)
    assert isinstance(ev, list) and len(ev) == 1
    assert isinstance(ev[0], tuple) and len(ev[0]) == 2
    assert s.goals(limit=5) == ["做AI Agent"]


def test_profile_set_get():
    s = _store()
    s.set_profile("summary", "用户喜欢猫")
    assert s.get_profile("summary") == "用户喜欢猫"
    assert s.get_profile("missing") is None


def test_persona_state_default_and_save():
    s = _store()
    st = s.load_state()
    assert st["trust"] == 70
    assert st["emotion"] == "calm"
    s.save_state(emotion="happy", trust=80)
    st2 = s.load_state()
    assert st2["emotion"] == "happy"
    assert st2["trust"] == 80


def test_save_and_read_vector():
    s = _store()
    mid = s.upsert_memory("fact", "向量测试", importance=3)
    vec = [0.1, 0.2, 0.3, 0.4]
    s.save_vector(mid, "test-model", vec)
    out = s.vector_of(mid)
    assert out is not None
    assert len(out) == 4
    assert abs(out[0] - 0.1) < 1e-6


def test_blacklist_add_and_judge():
    s = _store()
    s.add_blacklist("秘密")
    assert s.is_blacklisted("这是我的秘密") is True
    assert s.is_blacklisted("公开信息") is False
    assert "秘密" in s.blacklist()


def test_delete_by_keyword_removes_and_blacklists():
    s = _store()
    s.upsert_memory("fact", "秘密爱好", importance=3)
    s.upsert_memory("fact", "公开爱好", importance=3)
    deleted = s.delete_by_keyword("秘密")
    assert deleted == 1
    remaining = s.memories(types=("fact",))
    assert len(remaining) == 1
    assert remaining[0]["content"] == "公开爱好"
    assert s.is_blacklisted("秘密爱好") is True


def test_companionship_cross_day_reset():
    s = _store()
    s.save_state(companionship_day="2000-01-01", companionship_seconds=1000)
    total = s.bump_companionship(30)  # 跨天应重置为 30
    assert total == 30
    st = s.load_state()
    assert st["companionship_seconds"] == 30
    total2 = s.bump_companionship(15)  # 同一天累加
    assert total2 == 45


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
