"""研究会话后端测试：表结构、协议存取、完整性检查、三态判定（全离线）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.application.research import ResearchRunner
from core.llm_client import LLMResponse
from core.memory import SQLiteMemoryStore
from core.research_session import (
    STATE_BLOCKED,
    STATE_CONTINUED,
    STATE_RESTARTED,
    ResearchSessionRepository,
)


class _Store:
    def __init__(self):
        self._conn = SQLiteMemoryStore(":memory:")
        self.store = self._conn


class _FakeAgent:
    def __init__(self):
        self.finished = []

    def begin_task(self, text):
        return SimpleNamespace(
            system_prompt="SYS",
            messages=[{"role": "user", "content": text}],
            emotion="calm",
        )

    def finish_task(self, text, *, emotion=""):
        self.finished.append(text)


class _LLM:
    def __init__(self, replies):
        self.calls = []
        self._replies = list(replies)

    def chat_with_server_tools(self, system, messages, tools=None):
        self.calls.append([dict(m) for m in messages])
        return self._replies.pop(0)


def _resp(content="答", calls=None):
    return LLMResponse(content=content, finish_reason="stop",
                       server_tool_calls=calls or [])


CALL_BLOCK = [{"index": 0, "calls": [
    {"id": "call_1", "type": "deepseek_read_url", "kind": "read_link",
     "status": "completed", "page": {"url": "https://a.com", "signature": "SIG"}}]}]


@pytest.fixture()
def store():
    s = SQLiteMemoryStore(":memory:")
    yield s
    s.close()


@pytest.fixture()
def repo(store):
    return ResearchSessionRepository(store, ttl_hours=24, max_chars=1000)


# ---------------------------------------------------------------- 存取

def test_create_and_get(repo):
    s = repo.create("conv-1")
    assert s.status == "active" and s.session_id.startswith("rs_")
    again = repo.get(s.session_id)
    assert again and again.session_id == s.session_id and again.turn_count == 0


def test_record_turn_persists_protocol(repo):
    s = repo.create()
    message = {"role": "assistant", "content": "答", "server_tool_calls": CALL_BLOCK}
    repo.record_turn(s.session_id, user_text="读一下", assistant_message=message,
                     sources=[{"url": "https://a.com", "title": "A"}])

    loaded = repo.get(s.session_id)
    assert loaded.turn_count == 1
    msg = loaded.protocol_message()
    assert msg["server_tool_calls"][0]["calls"][0]["id"] == "call_1"
    assert loaded.last_user_text() == "读一下"
    assert loaded.sources[0]["url"] == "https://a.com"


def test_check_accepts_valid_session(repo):
    s = repo.create()
    repo.record_turn(s.session_id, user_text="u",
                     assistant_message={"role": "assistant", "content": "a",
                                        "server_tool_calls": CALL_BLOCK})
    ok, reason = repo.check(repo.get(s.session_id))
    assert ok, reason


def test_check_rejects_missing_tool_calls(repo):
    s = repo.create()
    repo.record_turn(s.session_id, user_text="u",
                     assistant_message={"role": "assistant", "content": "a"})
    ok, reason = repo.check(repo.get(s.session_id))
    assert not ok and reason == "no_tool_calls"


def test_check_rejects_expired(store, repo):
    s = repo.create()
    store._conn.execute("UPDATE research_session SET expires_at=? WHERE session_id=?",
                        ("2000-01-01T00:00:00+00:00", s.session_id))
    store._conn.commit()
    ok, reason = repo.check(repo.get(s.session_id))
    assert not ok and reason in ("expired", "status_expired")


def test_cleanup_removes_expired(store, repo):
    s = repo.create()
    store._conn.execute("UPDATE research_session SET expires_at=? WHERE session_id=?",
                        ("2000-01-01T00:00:00+00:00", s.session_id))
    store._conn.commit()
    assert repo.cleanup() >= 1
    assert repo.get(s.session_id) is None


# ---------------------------------------------------------------- 三态

def test_first_turn_has_no_state(repo):
    llm = _LLM([_resp()])
    runner = ResearchRunner(agent=_FakeAgent(), llm=llm, tools=[{"type": "x"}],
                            sessions=repo)
    result = runner.run("第一个问题")
    assert result.ok and result.session_state == ""
    assert len(llm.calls[0]) == 1  # 只有本轮用户消息


def test_second_turn_continues_with_protocol(repo):
    llm = _LLM([
        _resp("第一次回答", CALL_BLOCK),
        _resp("第二次回答", []),
    ])
    runner = ResearchRunner(agent=_FakeAgent(), llm=llm, tools=[{"type": "x"}],
                            sessions=repo)
    runner.run("读一下这个页面")
    second = runner.run("刚才那页说了什么")

    assert second.session_state == STATE_CONTINUED
    sent = llm.calls[1]
    # user(上一轮) → assistant(带 protocol) → user(本轮)
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert sent[1]["server_tool_calls"][0]["calls"][0]["id"] == "call_1"
    assert sent[2]["content"] == "刚才那页说了什么"


def test_restarts_when_protocol_missing(repo):
    llm = _LLM([
        _resp("第一次回答", []),      # 没发生工具调用 → 没有可回传的协议
        _resp("第二次回答", []),
    ])
    runner = ResearchRunner(agent=_FakeAgent(), llm=llm, tools=[{"type": "x"}],
                            sessions=repo)
    runner.run("第一次")
    second = runner.run("第二次")

    assert second.session_state == STATE_RESTARTED
    assert len(llm.calls[1]) == 1  # 没有把无协议的历史塞回去


def test_blocked_when_auto_restart_disabled(repo):
    llm = _LLM([_resp("第一次回答", [])])
    runner = ResearchRunner(agent=_FakeAgent(), llm=llm, tools=[{"type": "x"}],
                            sessions=repo, auto_restart=False)
    runner.run("第一次")
    second = runner.run("第二次")

    assert not second.ok
    assert second.session_state == STATE_BLOCKED
    assert len(llm.calls) == 1  # 没有发出第二次请求
