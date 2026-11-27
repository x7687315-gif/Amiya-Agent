"""AgentRuntime：多轮模型-工具循环（2.x Phase 3B 核心）。

契约：ARCHITECTURE_2.0.md §3.5/§7 · INTERFACE_CONTRACTS_2.0.md §6。
职责边界（红线）：
- 只组织执行流程：不拼 Persona、不检索 Memory、不碰 SQLite 底层、
  不决定长期记忆写入、不操作 Flet、不实现 DeepSeek 协议细节。
- ContextAssembler 产出的 system_prompt 直接用；工具执行走 ToolExecutor；
  持久化走 RunRepository；观察走 EventBus。
- 硬预算：MAX_STEPS=5、每轮工具 ≤4；取消 Event 在循环边界检查。
- 思考+tools 的 reasoning_content：消息快照必须保留 assistant 的该字段
  （IMPL-02 实测：缺失则 400，空串可放行）——引擎原样追加 LLMResponse 的
  完整 message（含 tool_calls/reasoning_content）。
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from core.context import AssembledContext
from core.llm_client import LLMError, LLMResponse
from core.tools import ToolExecutor
from core.tools.permissions import Approver

from .events import EventBus, EventType
from .state import RunStateMachine, RunStatus
from .store import RunRepository

log = logging.getLogger(__name__)

MAX_STEPS = 5
MAX_TOOL_CALLS_PER_STEP = 4


@dataclass(frozen=True)
class RunResult:
    run_id: str
    status: RunStatus
    final_response: str
    error: str = ""
    steps_used: int = 0


class BudgetExceeded(RuntimeError):
    pass


class _EventedApprover:
    """给 L2 审批加事件与状态外显（Phase 5A）。

    Runtime 不持审批状态（红线：只组织流程），本包装器只做两件事：
    1. 审批前后把 run 状态在 waiting_approval / running 之间切换；
    2. 发 approval_required / approval_decided 事件供 UI 与 Trace 观察。

    异常语义与 PermissionManager 一致：内层抛错则 outcome 记 unavailable 后**原样抛出**，
    由 PermissionManager 收敛为拒绝（这里不吞异常、不放行）。
    """

    def __init__(self, bus: EventBus, repo: RunRepository, run_id: str, inner: Approver) -> None:
        self._bus = bus
        self._repo = repo
        self._run_id = run_id
        self._inner = inner

    def ask(self, *, tool_name: str, reason: str, args: dict):
        try:
            self._repo.set_status(self._run_id, RunStatus.WAITING_APPROVAL)
        except Exception as exc:  # noqa: BLE001 - 状态外显失败不影响审批本身
            log.warning("切换 waiting_approval 失败（忽略）：%s", exc)
        self._bus.emit(self._run_id, EventType.APPROVAL_REQUIRED,
                       {"tool": tool_name, "reason": reason, "arguments": args})
        try:
            outcome = self._inner.ask(tool_name=tool_name, reason=reason, args=args)
        except Exception:
            self._bus.emit(self._run_id, EventType.APPROVAL_DECIDED,
                           {"tool": tool_name, "outcome": "unavailable"})
            raise
        finally:
            try:
                self._repo.set_status(self._run_id, RunStatus.RUNNING)
            except Exception as exc:  # noqa: BLE001
                log.warning("恢复 running 状态失败（忽略）：%s", exc)
        self._bus.emit(self._run_id, EventType.APPROVAL_DECIDED,
                       {"tool": tool_name, "outcome": getattr(outcome, "value", str(outcome))})
        return outcome


class AgentRuntime:
    """Run 执行引擎。所有外部依赖通过构造注入，便于离线测试。"""

    def __init__(
        self,
        *,
        llm,                         # ToolCapableLLM（运行时 duck typing）
        executor: ToolExecutor,
        repo: RunRepository,
        bus: EventBus | None = None,
        max_steps: int = MAX_STEPS,
        max_tool_calls: int = MAX_TOOL_CALLS_PER_STEP,
    ) -> None:
        self._llm = llm
        self._executor = executor
        self._repo = repo
        self._bus = bus or EventBus()
        self._max_steps = max_steps
        self._max_tool_calls = max_tool_calls
        self._cancels: dict[str, threading.Event] = {}
        self._cancels_lock = threading.RLock()

    @property
    def bus(self) -> EventBus:
        return self._bus

    def subscribe(self, callback: Callable) -> Callable[[], None]:
        return self._bus.subscribe(callback)

    def cancel(self, run_id: str) -> None:
        """在循环边界生效：置 Event；在途请求自然结束（与 batch1 语义一致）。"""
        with self._cancels_lock:
            ev = self._cancels.get(run_id)
            if ev is not None:
                ev.set()

    def run(
        self,
        ctx: AssembledContext,
        *,
        session_id: str,
        tools: list[dict],
        trigger: str = "task",
        handoff_from: str | None = None,
        approver: Approver | None = None,
        cancel: threading.Event | None = None,
    ) -> RunResult:
        """执行一个完整 Run：创建记录 → 多轮循环 → 终态落库 + 事件。"""
        run_id = self._repo.create_run(
            session_id, trigger=trigger, handoff_from=handoff_from,
            budget_steps=self._max_steps,
        )
        with self._cancels_lock:
            self._cancels[run_id] = cancel or threading.Event()
        self._repo.start_run(run_id)
        self._bus.emit(run_id, EventType.RUN_STARTED,
                       {"trigger": trigger, "budget": self._max_steps})

        # 构造消息：会话窗口 + 本轮用户消息（system_prompt 单独传）
        messages: list[dict] = list(ctx.messages)
        # L2 审批外显包装：仅在调用方提供 approver 时启用（缺失 = fail-closed 拒绝）
        effective_approver: Approver | None = (
            _EventedApprover(self._bus, self._repo, run_id, approver)
            if approver is not None else None
        )
        seq = 1
        try:
            for step in range(1, self._max_steps + 1):
                if self._is_cancelled(run_id):
                    return self._finish(run_id, RunStatus.CANCELLED, "",
                                        "用户取消", seq - 1)

                # --- 模型请求 step ---
                step_id = self._repo.add_step(run_id, seq, "model_request", "running")
                self._bus.emit(run_id, EventType.THINKING_STARTED, {"step": step})
                try:
                    resp: LLMResponse = self._llm.chat_with_tools(
                        ctx.system_prompt, messages, tools=tools,
                        tool_choice="auto", cancel=self._cancels[run_id],
                    )
                except LLMError as exc:
                    if exc.kind == "cancelled":
                        self._repo.finish_step(step_id, "cancelled", error_code=exc.kind)
                        return self._finish(run_id, RunStatus.CANCELLED, "", "用户取消", step)
                    self._repo.finish_step(step_id, "failed", error_code=exc.kind,
                                           result_summary=str(exc)[:4000])
                    self._bus.emit(run_id, EventType.RUN_FAILED, {"error": exc.kind})
                    return self._finish(run_id, RunStatus.FAILED, "", str(exc), step)
                self._repo.finish_step(
                    step_id, "ok",
                    result_summary=f"finish={resp.finish_reason}; "
                                   f"tool_calls={len(resp.tool_calls)}",
                )
                seq += 1

                # 无工具调用 → 最终回答
                if not resp.tool_calls:
                    final = resp.content or ""
                    self._repo.add_step(run_id, seq, "final_answer", "ok",
                                        result_summary=final[:4000])
                    self._bus.emit(run_id, EventType.RESPONSE_COMPLETED,
                                   {"text": final})
                    return self._finish(run_id, RunStatus.COMPLETED, final, "", seq)

                # 有工具调用 → 串行执行（每轮 ≤ max_tool_calls）
                assistant_msg = self._response_to_message(resp)
                messages.append(assistant_msg)
                calls = resp.tool_calls[:self._max_tool_calls]
                for tc in calls:
                    if self._is_cancelled(run_id):
                        return self._finish(run_id, RunStatus.CANCELLED, "", "用户取消", seq)
                    self._bus.emit(run_id, EventType.TOOL_REQUESTED,
                                   {"call_id": tc.id, "name": tc.name,
                                    "arguments": tc.arguments})
                    tool_step = self._repo.add_step(
                        run_id, seq, "tool_call", "running",
                        tool_name=tc.name, tool_call_id=tc.id, arguments=tc.arguments,
                    )
                    seq += 1
                    result = self._executor.execute(
                        tc.id, tc.name, tc.arguments,
                        approver=effective_approver, cancel=self._cancels[run_id],
                        on_start=lambda _n=tc.name, _i=tc.id: self._bus.emit(
                            run_id, EventType.TOOL_STARTED,
                            {"call_id": _i, "name": _n}),
                    )
                    self._repo.finish_step(
                        tool_step, "ok" if result.ok else "failed",
                        result_summary=result.content, error_code=result.error_code,
                    )
                    if result.ok:
                        self._bus.emit(run_id, EventType.TOOL_COMPLETED,
                                       {"call_id": tc.id, "name": tc.name,
                                        "summary": result.content[:200]})
                    else:
                        self._bus.emit(run_id, EventType.TOOL_FAILED,
                                       {"call_id": tc.id, "name": tc.name,
                                        "error": result.error_code,
                                        "detail": result.content[:200]})
                    # role:tool 回传（CC 协议尾部追加，IMPL-02 reasoning 字段保留在 assistant_msg）
                    messages.append({
                        "role": "tool", "tool_call_id": tc.id, "content": result.content,
                    })
            # 预算耗尽
            self._bus.emit(run_id, EventType.RUN_FAILED, {"error": "budget_exceeded"})
            return self._finish(run_id, RunStatus.FAILED, "", "budget_exceeded", self._max_steps)
        finally:
            with self._cancels_lock:
                self._cancels.pop(run_id, None)

    @staticmethod
    def _response_to_message(resp: LLMResponse) -> dict:
        """把 LLMResponse 转为 assistant 消息。

        IMPL-02：思考+tools 时 reasoning_content 字段必须存在（缺失 400）。
        LLMResponse 未显式携带该字段，这里注入空串占位（实测 200 可放行），
        保证后续轮次 payload 合法；未来若 LLMResponse 增字段再原样透传。
        """
        msg: dict = {"role": "assistant", "content": resp.content}
        if resp.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name,
                              "arguments": __import__("json").dumps(
                                  tc.arguments, ensure_ascii=False)}}
                for tc in resp.tool_calls
            ]
            msg["reasoning_content"] = ""  # 字段存在性（IMPL-02）
        return msg

    def _is_cancelled(self, run_id: str) -> bool:
        with self._cancels_lock:
            ev = self._cancels.get(run_id)
        return ev is not None and ev.is_set()

    def _finish(
        self, run_id: str, status: RunStatus, final: str, error: str, steps: int,
    ) -> RunResult:
        self._repo.set_status(
            run_id, status, completed_at=self._now_iso(),
            final_response=final or None, error=error or None,
        )
        if status is RunStatus.CANCELLED:
            self._bus.emit(run_id, EventType.RUN_CANCELLED, {"reason": error})
        elif status is RunStatus.COMPLETED:
            self._bus.emit(run_id, EventType.RUN_COMPLETED, {"steps": steps})
        return RunResult(run_id, status, final, error, steps)

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime
        return datetime.now().isoformat(timespec="seconds")
