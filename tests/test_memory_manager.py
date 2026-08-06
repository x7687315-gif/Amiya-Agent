"""MemoryManager（Step 2.2 中间层）单测：只覆盖对话记录闭环。

检索 / 手动记忆写入 / LLM 抽取不在本步，故不在此测试。
"""
import pytest

from core.memory import MemoryManager, SQLiteMemoryStore, new_session_id


@pytest.fixture()
def store():
    s = SQLiteMemoryStore(":memory:")
    yield s
    s.close()


@pytest.fixture()
def mgr(store):
    return MemoryManager(store, session_id="sess-test")


def test_session_id_defaults_unique():
    a, b = new_session_id(), new_session_id()
    assert a != b
    assert len(a) > 10


def test_add_turn_persists_and_returns_id(mgr, store):
    uid = mgr.add_turn("user", "你好")
    aid = mgr.add_turn("assistant", "用户，助手在。")
    assert uid > 0 and aid > uid
    rows = store.recent_messages(limit=10)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[0]["content"] == "你好"


def test_add_turn_rejects_unknown_role(mgr):
    with pytest.raises(ValueError):
        mgr.add_turn("system", "不该被写进对话表")


def test_add_turn_skips_blank_content(mgr, store):
    assert mgr.add_turn("assistant", "   ") == 0
    assert mgr.add_turn("user", "") == 0
    assert store.recent_messages(limit=10) == []


def test_user_turn_bumps_total_turns_only(mgr):
    mgr.add_turn("user", "第一句")
    mgr.add_turn("assistant", "回应")
    mgr.add_turn("user", "第二句")
    state = mgr.state()
    assert state["total_turns"] == 2  # 助手消息不计轮次
    assert state["last_seen_at"]  # 已刷新


def test_recent_turns_is_chronological(mgr):
    for i in range(5):
        mgr.add_turn("user", f"第{i}句")
    turns = mgr.recent_turns(limit=3)
    assert [t["content"] for t in turns] == ["第2句", "第3句", "第4句"]


def test_new_session_rotates_without_deleting(mgr, store):
    mgr.add_turn("user", "旧会话的话")
    old = mgr.session_id
    new = mgr.new_session()
    assert new != old
    mgr.add_turn("user", "新会话的话")
    # 历史仍在库里，跨会话可回溯
    assert len(store.recent_messages(limit=10)) == 2


def test_bump_companionship_accumulates(mgr):
    assert mgr.bump_companionship(60) == 60
    assert mgr.bump_companionship(30) == 90
    assert mgr.state()["companionship_seconds"] == 90


def test_manager_never_touches_sql_directly(mgr):
    """分层守卫：Agent 只能通过本层拿数据，管理器不得暴露连接对象。"""
    assert not hasattr(mgr, "_conn")
    assert not hasattr(mgr, "execute")
