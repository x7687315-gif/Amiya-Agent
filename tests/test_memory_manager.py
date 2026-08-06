"""MemoryManager 中间层单测。

Step 2.2 覆盖对话记录闭环；Step 2.3 覆盖手动记忆与人工确认队列。
检索（2.4）/ Prompt 注入（2.5）/ LLM 抽取（2.7）不在本步，故不在此测试。
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


# ---------- Step 2.3：手动记忆 ----------


def test_remember_and_list(mgr):
    mid = mgr.remember("fact", "  用户养了一只猫  ", importance=7)
    assert mid > 0
    rows = mgr.list_memories()
    assert len(rows) == 1
    assert rows[0]["content"] == "用户养了一只猫"  # 前后空白被清掉
    assert rows[0]["importance"] == 7


def test_remember_rejects_blank_and_bad_type(mgr):
    assert mgr.remember("fact", "   ") == 0
    with pytest.raises(ValueError):
        mgr.remember("nonsense", "内容")


def test_remember_clamps_out_of_range_weights(mgr):
    mid = mgr.remember("goal", "越界权重", importance=99, confidence=-3)
    row = mgr.list_memories()[0]
    assert row["id"] == mid
    assert row["importance"] == 10
    assert row["confidence"] == 1


def test_blacklist_blocks_write_at_entry(mgr, store):
    store.add_blacklist("身份证")
    assert mgr.remember("fact", "我的身份证号是123") == 0
    assert mgr.list_memories() == []  # 根本没落盘，而不是检索时才过滤


def test_edit_and_confirm_memory(mgr):
    mid = mgr.remember("fact", "喜欢狗")
    assert mgr.edit_memory(mid, content="喜欢猫", importance=8) is True
    assert mgr.list_memories()[0]["content"] == "喜欢猫"
    assert mgr.confirm_memory(mid) is True
    with pytest.raises(ValueError):
        mgr.edit_memory(mid, type="nonsense")


def test_forget_id_removes_single_without_blacklist(mgr):
    mid = mgr.remember("fact", "临时的事")
    assert mgr.forget_id(mid) is True
    assert mgr.list_memories() == []
    assert mgr.blacklist() == []


def test_forget_keyword_deletes_blacklists_and_purges_queue(mgr):
    mgr.remember("fact", "秘密计划A")
    mgr.propose("event", "秘密计划B")
    mgr.propose("event", "公开计划C")

    # 返回值 = 正表 1 条 + 队列 1 条，反映用户视角的总影响面
    assert mgr.forget("秘密") == 2
    assert mgr.list_memories() == []
    assert "秘密" in mgr.blacklist()
    # 队列里同主题的待确认项必须一并清掉，否则还会再问一次
    assert [c["content"] for c in mgr.pending_candidates()] == ["公开计划C"]
    # 之后同主题内容再也写不进来
    assert mgr.remember("fact", "另一个秘密") == 0
    assert mgr.propose("event", "又一个秘密") == 0


# ---------- Step 2.3：人工确认队列 ----------


def test_propose_then_confirm_enters_memory(mgr):
    cid = mgr.propose("preference", "喜欢机械键盘", importance=6, reason="提过两次")
    assert cid > 0
    assert mgr.list_memories() == []  # 确认前绝不进正表

    mid = mgr.confirm_candidate(cid)
    assert mid > 0
    assert mgr.list_memories()[0]["content"] == "喜欢机械键盘"
    assert mgr.pending_candidates() == []


def test_rejected_candidate_does_not_nag(mgr):
    cid = mgr.propose("preference", "喜欢晴天")
    assert mgr.reject_candidate(cid) is True
    assert mgr.propose("preference", "喜欢晴天") == 0
    assert mgr.pending_candidates() == []
    assert mgr.list_memories() == []


def test_propose_validates_type_and_blank(mgr):
    assert mgr.propose("fact", "  ") == 0
    with pytest.raises(ValueError):
        mgr.propose("nonsense", "内容")


def test_confirm_invalid_candidate_returns_zero(mgr):
    assert mgr.confirm_candidate(9999) == 0
    assert mgr.reject_candidate(9999) is False


def test_forget_counts_queue_only_hits(mgr):
    """只在队列里、尚未确认的内容被遗忘时，也要报告非零影响面。"""
    mgr.propose("event", "秘密安排")
    assert mgr.forget("秘密") == 1
    assert mgr.pending_candidates() == []


def test_forget_blank_keyword_is_noop(mgr):
    mgr.remember("fact", "保留这条")
    assert mgr.forget("   ") == 0
    assert mgr.blacklist() == []  # 不得把空串写进黑名单（会拦截一切）
    assert len(mgr.list_memories()) == 1
