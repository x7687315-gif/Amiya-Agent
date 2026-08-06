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


# ---------- Step 2.3：记忆编辑 + 候选队列 ----------


def test_update_memory_fields_and_miss():
    s = _store()
    mid = s.upsert_memory("fact", "喜欢狗", importance=3, confidence=3)
    assert s.update_memory(mid, content="喜欢猫", importance=7) is True
    row = s.memory(mid)
    assert row["content"] == "喜欢猫"
    assert row["importance"] == 7
    assert row["confidence"] == 3  # 未传的字段保持原值
    assert s.update_memory(9999, content="不存在") is False
    assert s.update_memory(mid) is False  # 没有任何字段可改


def test_delete_memory_by_id_does_not_blacklist():
    s = _store()
    mid = s.upsert_memory("fact", "临时记忆", importance=3)
    assert s.delete_memory(mid) is True
    assert s.memory(mid) is None
    assert s.blacklist() == []  # 删单条 != 永久屏蔽话题
    assert s.delete_memory(mid) is False


def test_confirm_memory_stamps_last_confirmed_at():
    s = _store()
    mid = s.upsert_memory("goal", "考研", importance=8)
    assert s.memory(mid)["last_confirmed_at"] is None
    assert s.confirm_memory(mid) is True
    assert s.memory(mid)["last_confirmed_at"]


def test_candidate_confirm_writes_into_memory():
    s = _store()
    cid = s.add_candidate("preference", "喜欢机械键盘", importance=6, reason="用户提过两次")
    assert cid > 0
    pending = s.pending_candidates()
    assert len(pending) == 1
    assert pending[0]["reason"] == "用户提过两次"

    mid = s.confirm_candidate(cid)
    assert mid > 0
    assert s.memory(mid)["content"] == "喜欢机械键盘"
    assert s.memory(mid)["last_confirmed_at"]  # 确认即等于用户背书
    assert s.pending_candidates() == []
    assert s.candidate(cid)["status"] == "confirmed"
    assert s.candidate(cid)["memory_id"] == mid


def test_candidate_confirm_twice_is_noop():
    s = _store()
    cid = s.add_candidate("fact", "养了一只猫")
    assert s.confirm_candidate(cid) > 0
    assert s.confirm_candidate(cid) == 0  # 已决策，不重复写
    assert len(s.memories(types=("fact",), limit=10)) == 1


def test_rejected_candidate_never_revives():
    s = _store()
    cid = s.add_candidate("preference", "喜欢晴天")
    assert s.reject_candidate(cid) is True
    assert s.pending_candidates() == []
    # 同内容再次提交不得复活为 pending，否则等于反复骚扰用户
    assert s.add_candidate("preference", "喜欢晴天") == 0
    assert s.pending_candidates() == []
    assert s.reject_candidate(cid) is False  # 已决策，不可重复


def test_duplicate_pending_candidate_updates_weight():
    s = _store()
    cid = s.add_candidate("event", "下个月去大学", importance=5)
    again = s.add_candidate("event", "下个月去大学", importance=9)
    assert again == cid  # 同一条，不产生第二行
    pending = s.pending_candidates()
    assert len(pending) == 1
    assert pending[0]["importance"] == 9  # 反复提到 → 更重要


def test_pending_candidates_ranked_by_importance():
    s = _store()
    s.add_candidate("fact", "低权重", importance=2)
    s.add_candidate("goal", "高权重", importance=9)
    assert [c["content"] for c in s.pending_candidates()] == ["高权重", "低权重"]


def test_purge_candidates_by_keyword():
    s = _store()
    s.add_candidate("fact", "关于秘密的事")
    s.add_candidate("fact", "公开的事")
    assert s.purge_candidates_by_keyword("秘密") == 1
    assert [c["content"] for c in s.pending_candidates()] == ["公开的事"]


def test_migration_v1_db_upgrades_to_v2(tmp_path):
    """已装机的 v1 库再次打开时应平滑升到 v2，且原数据不丢。"""
    import sqlite3

    from core.memory.store import _SCHEMA_V1

    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA_V1)
    conn.execute("PRAGMA user_version=1")
    conn.execute(
        "INSERT INTO memory(type, content, created_at, updated_at) "
        "VALUES('fact','旧库里的记忆','2026-01-01','2026-01-01')"
    )
    conn.commit()
    conn.close()

    s = SQLiteMemoryStore(db)
    try:
        assert s._conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert s.memories(types=("fact",))[0]["content"] == "旧库里的记忆"
        assert s.add_candidate("fact", "升级后可用") > 0  # v2 新表已建
    finally:
        s.close()


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
