"""Phase 5A 测试：审批桥 + 任务执行器 + 事件精度（全离线，无真实 API）。

覆盖目标：
1. ApprovalBridge 的 fail-closed 路径（超时/回调异常/关闭/取消）与幂等决策；
2. Agent.begin_task / finish_task 与快路径等价（落库、窗口、空回复跳过）；
3. TaskRunner 端到端：真实内置工具 + 事件序列 + 单飞 + 取消唤醒待决审批。
"""
from __future__ import annotations

import threading
import time

import pytest

from core.agent import Agent
from core.application import ApprovalBridge, ApprovalRequest, TaskRunner, build_runtime_stack
from core.llm_client import LLMResponse, ToolCall
from core.memory import MemoryManager, SQLiteMemoryStore
from core.persona import load_persona
from core.runtime import EventType, RunStatus
from core.tools.permissions import ApprovalOutcome


# ---------------------------------------------------------------- 工具函数

class _ScriptedLLM:
    """按脚本返回：tool_calls 轮 → … → 最终文本。"""

    def __init__(self, script):
        self.script = list(script)
        self.payloads: list = []

    def chat_with_tools(self, system, messages, **kwargs):
        self.payloads.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _store():
    return SQLiteMemoryStore(":memory:")


# ---------------------------------------------------------------- ApprovalBridge

def test_bridge_allows_once():
    seen: list[ApprovalRequest] = []
    bridge = ApprovalBridge(lambda req, resolve: (seen.append(req), resolve(
        ApprovalOutcome.ALLOWED_ONCE)))

    out = bridge.ask(tool_name="delete_task", reason="需要确认", args={"task_id": "t1"})

    assert out is ApprovalOutcome.ALLOWED_ONCE
    assert seen and seen[0].tool_name == "delete_task"
    assert seen[0].request_id and seen[0].created_at
    assert bridge.pending_count == 0  # 决策后不留残槽


def test_bridge_timeout_denies():
    bridge = ApprovalBridge(lambda req, resolve: None, timeout_s=0.05)
    t0 = time.monotonic()
    out = bridge.ask(tool_name="x", reason="r", args={})
    assert out is ApprovalOutcome.UNAVAILABLE
    assert time.monotonic() - t0 < 1.0  # 不会拖满真实等待
    assert bridge.pending_count == 0


def test_bridge_callback_error_denies():
    def boom(req, resolve):
        raise RuntimeError("ui gone")

    bridge = ApprovalBridge(boom)
    assert bridge.ask(tool_name="x", reason="r", args={}) is ApprovalOutcome.UNAVAILABLE


def test_bridge_reject_all_unblocks_waiter():
    bridge = ApprovalBridge(lambda req, resolve: None, timeout_s=5.0)
    result: list[ApprovalOutcome] = []

    def worker():
        result.append(bridge.ask(tool_name="x", reason="r", args={}))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    deadline = time.monotonic() + 2
    while bridge.pending_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert bridge.reject_all() == 1
    t.join(timeout=2)
    assert result == [ApprovalOutcome.REJECTED]


def test_bridge_resolve_is_idempotent_and_rejects_unknown():
    holder: dict = {}
    bridge = ApprovalBridge(lambda req, resolve: holder.update(resolve=resolve))
    out: list = []
    t = threading.Thread(target=lambda: out.append(
        bridge.ask(tool_name="x", reason="r", args={})), daemon=True)
    t.start()
    deadline = time.monotonic() + 2
    while "resolve" not in holder and time.monotonic() < deadline:
        time.sleep(0.01)

    resolve = holder["resolve"]
    # 通过公开 API 用未知 id 提交 → False；真实 id 走闭包 resolver
    assert bridge.resolve("nope", ApprovalOutcome.REJECTED) is False
    resolve(ApprovalOutcome.ALLOWED_ONCE)
    t.join(timeout=2)
    assert out == [ApprovalOutcome.ALLOWED_ONCE]
    # 重复决议（同一请求已弹出）必须失败，不能翻案
    assert bridge.resolve("nope", ApprovalOutcome.ALLOWED_ONCE) is False


def test_bridge_close_is_permanent():
    bridge = ApprovalBridge(lambda req, resolve: resolve(ApprovalOutcome.ALLOWED_ONCE))
    bridge.close()
    assert bridge.ask(tool_name="x", reason="r", args={}) is ApprovalOutcome.UNAVAILABLE


# ---------------------------------------------------------------- Agent 任务路径

class _PlainLLM:
    def stream_chat(self, system, history):
        yield "好"


def test_agent_begin_task_persists_user_and_returns_window():
    store = _store()
    memory = MemoryManager(store)
    agent = Agent(persona=load_persona(), llm=_PlainLLM(), memory=memory)

    ctx = agent.begin_task("帮我查一下时间")

    assert ctx.system_prompt and ctx.messages
    assert ctx.messages[-1] == {"role": "user", "content": "帮我查一下时间"}
    rows = store._conn.execute(
        "SELECT role,content FROM conversation ORDER BY id").fetchall()
    assert [(r["role"], r["content"]) for r in rows] == [
        ("user", "帮我查一下时间")]
    assert agent.session_id == memory.session_id
    store.close()


def test_agent_finish_task_persists_assistant_and_skips_blank():
    store = _store()
    memory = MemoryManager(store)
    agent = Agent(persona=load_persona(), llm=_PlainLLM(), memory=memory)
    agent.begin_task("你好")

    agent.finish_task("   ")  # 空回复（取消/失败）不写空消息
    rows = store._conn.execute("SELECT role FROM conversation").fetchall()
    assert [r["role"] for r in rows] == ["user"]

    agent.finish_task("已经办好了")
    rows = store._conn.execute(
        "SELECT role,content FROM conversation ORDER BY id").fetchall()
    assert [(r["role"], r["content"]) for r in rows] == [
        ("user", "你好"), ("assistant", "已经办好了")]
    assert agent._history[-1] == {"role": "assistant", "content": "已经办好了"}
    store.close()


def test_agent_finish_task_fires_on_reply_complete():
    got: list[str] = []
    agent = Agent(persona=load_persona(), llm=_PlainLLM(),
                  on_reply_complete=got.append)
    agent.finish_task("完成")
    assert got == ["完成"]


# ---------------------------------------------------------------- TaskRunner 端到端

def _runner(store, llm, *, approver=None):
    stack = build_runtime_stack(llm=llm, store=store)
    memory = MemoryManager(store)
    agent = Agent(persona=load_persona(), llm=llm, memory=memory)
    return TaskRunner(agent=agent, stack=stack, approver=approver), agent, stack


def test_task_runner_end_to_end_with_real_tool():
    store = _store()
    llm = _ScriptedLLM([
        LLMResponse(None, [ToolCall("call_1", "get_time", {})], "tool_calls"),
        LLMResponse("现在是下午三点。", [], "stop"),
    ])
    runner, agent, stack = _runner(store, llm)
    events: list = []

    result = runner.run("现在几点？", on_event=events.append)

    assert result.status is RunStatus.COMPLETED
    assert result.final_response == "现在是下午三点。"
    types = [e.type for e in events]
    # 事件精度：requested → started → completed 三段齐全，且全部属于同一 run
    for name in (EventType.RUN_STARTED, EventType.THINKING_STARTED,
                 EventType.TOOL_REQUESTED, EventType.TOOL_STARTED,
                 EventType.TOOL_COMPLETED, EventType.RESPONSE_COMPLETED,
                 EventType.RUN_COMPLETED):
        assert name in types, f"缺少事件 {name}"
    assert {e.run_id for e in events} == {result.run_id}
    # tool 事件带 call_id，UI 才能精确对行（不靠序号猜）
    tool_events = [e for e in events if e.type == EventType.TOOL_STARTED]
    assert tool_events[0].data["call_id"] == "call_1"
    # 落库：run + step + 对话双写
    run_row = store._conn.execute(
        "SELECT status FROM agent_run WHERE run_id=?", (result.run_id,)).fetchone()
    assert run_row["status"] == "completed"
    assert len(stack.repo.list_steps(result.run_id)) >= 2
    conv = store._conn.execute(
        "SELECT role FROM conversation ORDER BY id").fetchall()
    assert [r["role"] for r in conv] == ["user", "assistant"]
    assert agent._history[-1]["content"] == "现在是下午三点。"
    store.close()


def test_task_runner_l2_without_approver_is_denied():
    store = _store()
    llm = _ScriptedLLM([
        LLMResponse(None, [ToolCall("c1", "delete_task", {"task_id": "t1"})], "tool_calls"),
        LLMResponse("删除失败了。", [], "stop"),
    ])
    runner, agent, stack = _runner(store, llm)
    events: list = []

    result = runner.run("删掉任务 t1", on_event=events.append)

    assert result.status is RunStatus.COMPLETED
    failed = [e for e in events if e.type == EventType.TOOL_FAILED]
    assert failed and failed[0].data["error"] == "permission_denied"
    store.close()


def test_task_runner_l2_with_bridge_records_approval_events():
    store = _store()
    llm = _ScriptedLLM([
        LLMResponse(None, [ToolCall("c1", "delete_task", {"task_id": "t1"})], "tool_calls"),
        LLMResponse("已取消。", [], "stop"),
    ])
    asked: list = []
    bridge = ApprovalBridge(lambda req, resolve: (asked.append(req),
                                                  resolve(ApprovalOutcome.ALLOWED_ONCE)))
    runner, agent, stack = _runner(store, llm, approver=bridge)
    events: list = []

    result = runner.run("删掉任务 t1", on_event=events.append)

    assert asked and asked[0].tool_name == "delete_task"
    types = [e.type for e in events]
    assert EventType.APPROVAL_REQUIRED in types
    assert EventType.APPROVAL_DECIDED in types
    decided = [e for e in events if e.type == EventType.APPROVAL_DECIDED][0]
    assert decided.data["outcome"] == "allowed_once"
    assert result.status is RunStatus.COMPLETED
    store.close()


def test_task_runner_cancel_unblocks_pending_approval():
    store = _store()
    llm = _ScriptedLLM([
        LLMResponse(None, [ToolCall("c1", "delete_task", {"task_id": "t1"})], "tool_calls"),
        LLMResponse("不该走到这里", [], "stop"),
    ])
    bridge = ApprovalBridge(lambda req, resolve: None, timeout_s=30.0)
    runner, agent, stack = _runner(store, llm, approver=bridge)
    box: list = []

    t = threading.Thread(target=lambda: box.append(runner.run("删掉任务 t1")), daemon=True)
    t.start()
    deadline = time.monotonic() + 3
    while bridge.pending_count == 0 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert runner.cancel() is True
    t.join(timeout=5)
    assert box and box[0].status is RunStatus.CANCELLED
    store.close()


def test_task_runner_single_flight():
    store = _store()
    llm = _ScriptedLLM([LLMResponse("ok", [], "stop")])
    runner, agent, stack = _runner(store, llm)
    runner._running = True  # 模拟已有任务在执行
    try:
        result = runner.run("再来一个")
        assert result.status is RunStatus.FAILED
        assert "已有任务" in result.error
    finally:
        runner._running = False
        store.close()
