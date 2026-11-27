"""TaskRunner：把一次「显式任务」封装成可观察、可取消的执行（2.x Phase 5A）。

职责边界：
- 只做**编排与收尾**：取上下文（Agent）→ 调 Runtime → 把最终回答交还 Agent 持久化；
- 不拼提示词、不碰 SQLite 细节、不操作 Flet（UI 通过 on_event 订阅）；
- 事件订阅是「观察」，任何订阅者异常由 EventBus 吞掉，不影响 Run。

为什么需要它而不是 UI 直接调 runtime.run：
1. 上下文与持久化都在 Agent 一侧（认知/记忆边界不越层）；
2. 取消要同时作用于「Runtime 循环」与「待决审批」，否则 worker 卡在对话框上；
3. 单飞约束（同一时刻只跑一个任务）与幂等集合重置需要一处集中判定。
"""
from __future__ import annotations

import logging
import threading
from typing import Callable, Optional

from core.agent import Agent
from core.runtime import RunResult, RunStatus
from core.tools.permissions import Approver

from .facade import RuntimeStack

log = logging.getLogger(__name__)


class TaskRunner:
    """显式任务执行器（单飞；线程安全）。"""

    def __init__(
        self,
        *,
        agent: Agent,
        stack: RuntimeStack,
        approver: Optional[Approver] = None,
    ) -> None:
        self._agent = agent
        self._stack = stack
        self._approver = approver
        self._lock = threading.RLock()
        self._cancel: Optional[threading.Event] = None
        self._running = False
        self._current_run_id = ""

    # ----- 状态 -----
    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    @property
    def current_run_id(self) -> str:
        with self._lock:
            return self._current_run_id

    # ----- 执行 -----
    def run(
        self,
        user_text: str,
        *,
        on_event: Optional[Callable] = None,
        trigger: str = "task",
    ) -> RunResult:
        """执行一次任务。返回 RunResult；错误已被 Runtime 收敛，不向 UI 抛异常。

        trigger 写入 `agent_run.trigger`：`task`（用户显式）/ `scheduler`（到期
        任务自动触发）。两者执行流程完全相同，只在审计上可区分来源。

        并发调用直接返回一个 FAILED 结果而不是阻塞——UI 侧本来就有 busy 态，
        这里只是不让第二个调用把第一个的取消 Event 覆盖掉。
        """
        with self._lock:
            if self._running:
                return RunResult("", RunStatus.FAILED, "", "已有任务在执行")
            self._running = True
            cancel = threading.Event()
            self._cancel = cancel

        dispose = None
        try:
            if on_event is not None:
                dispose = self._stack.bus.subscribe(on_event)
            # 跨 Run 的 tool_call_id 可能重复（模型侧 id 生成策略不保证全局唯一），
            # 幂等集合只应约束「同一次 Run 内不重复执行」。
            self._stack.executor.reset_idempotency()
            ctx = self._agent.begin_task(user_text)
            result = self._stack.runtime.run(
                ctx,
                session_id=self._agent.session_id or "local",
                tools=self._stack.registry.schemas(),
                trigger=trigger,
                approver=self._approver,
                cancel=cancel,
            )
            with self._lock:
                self._current_run_id = result.run_id
            # 收尾与快路径等价：落 assistant 消息 + 入窗口 + 情绪持久化 + 完成通知
            self._agent.finish_task(result.final_response, emotion=ctx.emotion)
            return result
        finally:
            if dispose is not None:
                try:
                    dispose()
                except Exception as e:  # noqa: BLE001
                    log.warning("事件退订失败（忽略）：%s", e)
            with self._lock:
                self._running = False
                self._cancel = None

    # ----- 取消 -----
    def cancel(self) -> bool:
        """请求取消：置 Runtime 的 cancel Event，并立即拒绝所有待决审批。

        只置 Event 不够——worker 此时可能正阻塞在审批对话框上，不 reject_all
        就要等满审批超时才会回到循环边界。
        """
        with self._lock:
            cancel = self._cancel
        if cancel is None:
            return False
        cancel.set()
        reject_all = getattr(self._approver, "reject_all", None)
        if callable(reject_all):
            try:
                reject_all()
            except Exception as e:  # noqa: BLE001 - 取消失败不阻断调用方
                log.warning("拒绝待决审批失败（忽略）：%s", e)
        return True
