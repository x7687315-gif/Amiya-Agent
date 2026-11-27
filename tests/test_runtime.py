"""Phase 3B 测试：状态机 + EventBus + RunRepository + Runtime 循环（全离线，fake LLM）。"""
import threading

import pytest

from core.context import AssembledContext
from core.llm_client import LLMError, LLMResponse, ToolCall
from core.memory.store import SQLiteMemoryStore
from core.runtime import (
    AgentEvent, AgentRuntime, EventBus, EventType, InvalidTransition,
    RunRepository, RunStateMachine, RunStatus,
)
from core.tools import ToolExecutor, ToolRegistry, ToolResult, ToolSpec
from core.tools.base import PermissionLevel


# ---------- 状态机 ----------

def test_state_machine_transitions():
    assert RunStateMachine.can(RunStatus.CREATED, RunStatus.RUNNING)
    assert RunStateMachine.can(RunStatus.RUNNING, RunStatus.WAITING_APPROVAL)
    assert RunStateMachine.can(RunStatus.WAITING_APPROVAL, RunStatus.RUNNING)
    assert not RunStateMachine.can(RunStatus.COMPLETED, RunStatus.RUNNING)
    assert not RunStateMachine.can(RunStatus.CANCELLED, RunStatus.COMPLETED)
    assert RunStateMachine.is_terminal(RunStatus.COMPLETED)
    with pytest.raises(InvalidTransition):
        RunStateMachine.require(RunStatus.COMPLETED, RunStatus.RUNNING)


# ---------- EventBus ----------

def test_event_bus_seq_and_subscriber_isolation():
    bus = EventBus()
    got = []
    bus.subscribe(got.append)
    bus.subscribe(lambda e: (_ for _ in ()).throw(RuntimeError("sub crash")))
    e1 = bus.emit("r1", EventType.RUN_STARTED, {"a": 1})
    e2 = bus.emit("r1", EventType.THINKING_STARTED)
    assert e1.seq == 1 and e2.seq == 2
    assert isinstance(e1, AgentEvent) and e1.ts
    assert len(got) == 2  # 崩溃订阅者不影响其他订阅者
    assert bus.emit("r2", EventType.RUN_STARTED).seq == 1  # 按 run 独立递增


def test_event_bus_disposer():
    bus = EventBus()
    got = []
    d = bus.subscribe(got.append)
    bus.emit("r", EventType.RUN_STARTED)
    d()
    bus.emit("r", EventType.RUN_STARTED)
    assert len(got) == 1


# ---------- RunRepository ----------

def test_run_repository_crud(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "run.db"))
    repo = RunRepository(store)
    rid = repo.create_run("s1", trigger="task")
    assert repo.get_run(rid).status is RunStatus.CREATED
    repo.start_run(rid)
    assert repo.get_run(rid).status is RunStatus.RUNNING
    s1 = repo.add_step(rid, 1, "model_request", "running")
    repo.finish_step(s1, "ok", result_summary="done")
    repo.set_status(rid, RunStatus.COMPLETED, final_response="最终", completed_at="now")
    assert repo.get_run(rid).status is RunStatus.COMPLETED
    steps = repo.list_steps(rid)
    assert len(steps) == 1 and steps[0].status == "ok"
    assert store._conn.execute("SELECT final_response FROM agent_run WHERE run_id=?",
                               (rid,)).fetchone()[0] == "最终"
    store.close()


# ---------- Fake LLM / Tool ----------

class _FakeTool:
    def __init__(self):
        self.spec = ToolSpec("fake", "fake tool", {"type": "object"}, PermissionLevel.L0,
                             timeout_s=2.0)
        self.calls = []

    def execute(self, args, *, cancel=None):
        self.calls.append(args)
        return ToolResult.success("fake-result")


class _ScriptedLLM:
    """按脚本返回：tool_calls 轮 → tool_calls 轮 → 最终文本。"""

    def __init__(self, script):
        self.script = list(script)
        self.payloads = []
        self.cancel_seen = []

    def chat_with_tools(self, system, messages, **kwargs):
        self.payloads.append(list(messages))
        self.cancel_seen.append(kwargs.get("cancel"))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ctx():
    return AssembledContext(system_prompt="SYS", messages=[{"role": "user", "content": "do it"}])


def _setup(tmp_path, llm, *, tool=None):
    store = SQLiteMemoryStore(str(tmp_path / "rt.db"))
    reg = ToolRegistry()
    t = tool or _FakeTool()
    reg.register(t)
    ex = ToolExecutor(reg)
    repo = RunRepository(store)
    bus = EventBus()
    rt = AgentRuntime(llm=llm, executor=ex, repo=repo, bus=bus)
    return store, rt, bus, repo, t


# ---------- Runtime 循环 ----------

def test_runtime_two_tool_rounds_then_final(tmp_path):
    tc1 = ToolCall("id1", "fake", {})
    tc2 = ToolCall("id2", "fake", {})
    llm = _ScriptedLLM([
        LLMResponse(None, [tc1], "tool_calls"),
        LLMResponse(None, [tc2], "tool_calls"),
        LLMResponse("全部完成", [], "stop"),
    ])
    store, rt, bus, repo, tool = _setup(tmp_path, llm)
    events = []
    bus.subscribe(events.append)

    result = rt.run(_ctx(), session_id="s1", tools=[tool.spec.white_listed_schema()])
    assert result.status is RunStatus.COMPLETED
    assert result.final_response == "全部完成"
    assert result.steps_used >= 4
    assert len(tool.calls) == 2
    types = [e.type for e in events]
    assert EventType.RUN_STARTED in types
    assert types.count(EventType.TOOL_COMPLETED) == 2
    assert types[-1] == EventType.RUN_COMPLETED
    # reasoning_content 字段存在性（IMPL-02）：每个 assistant.tool_calls 消息都带该字段
    for payload in llm.payloads[1:]:
        for m in payload:
            if m.get("role") == "assistant" and m.get("tool_calls"):
                assert "reasoning_content" in m
    store.close()


def test_runtime_no_tool_final(tmp_path):
    llm = _ScriptedLLM([LLMResponse("直接回答", [], "stop")])
    store, rt, bus, repo, tool = _setup(tmp_path, llm)
    result = rt.run(_ctx(), session_id="s1", tools=[tool.spec.white_listed_schema()])
    assert result.status is RunStatus.COMPLETED and result.final_response == "直接回答"
    assert len(tool.calls) == 0
    store.close()


def test_runtime_budget_exceeded(tmp_path):
    tc = ToolCall("idX", "fake", {})
    llm = _ScriptedLLM([LLMResponse(None, [tc], "tool_calls") for _ in range(10)])
    store, rt, bus, repo, tool = _setup(tmp_path, llm)
    result = rt.run(_ctx(), session_id="s1", tools=[tool.spec.white_listed_schema()])
    assert result.status is RunStatus.FAILED and "budget" in result.error
    assert result.steps_used == rt._max_steps
    store.close()


def test_runtime_cancel_at_boundary(tmp_path):
    # 脚本：第一次工具调用后取消，第二次循环边界应识别
    tc = ToolCall("idY", "fake", {})
    llm = _ScriptedLLM([
        LLMResponse(None, [tc], "tool_calls"),
        LLMResponse(None, [tc], "tool_calls"),
    ])
    store, rt, bus, repo, tool = _setup(tmp_path, llm)
    ev = threading.Event()
    original = llm.chat_with_tools

    def cancel_after_first(*a, **kw):
        r = original(*a, **kw)
        ev.set()
        return r

    llm.chat_with_tools = cancel_after_first
    result = rt.run(_ctx(), session_id="s1", tools=[tool.spec.white_listed_schema()],
                    cancel=ev)
    assert result.status is RunStatus.CANCELLED
    store.close()


def test_runtime_llm_error_classified(tmp_path):
    llm = _ScriptedLLM([LLMError("401 bad key", "auth", 401)])
    store, rt, bus, repo, tool = _setup(tmp_path, llm)
    events = []
    bus.subscribe(events.append)
    result = rt.run(_ctx(), session_id="s1", tools=[tool.spec.white_listed_schema()])
    assert result.status is RunStatus.FAILED and "bad key" in result.error
    assert EventType.RUN_FAILED in [e.type for e in events]
    store.close()
