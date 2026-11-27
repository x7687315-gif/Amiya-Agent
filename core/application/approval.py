"""ApprovalBridge：同步 Approver 协议 ↔ 异步 UI 的线程安全桥（2.x Phase 5A）。

契约：INTERFACE_CONTRACTS_2.0.md §5（`Approver.ask` 为**同步**调用）。

为什么需要桥：
- `PermissionManager.check` 在 worker 线程里同步调 `approver.ask(...)`，
  必须**阻塞**到用户点「允许 / 拒绝」才能返回 outcome；
- 对话框只能由 UI 线程创建与操作；
- 于是桥在 worker 线程登记一个待决槽位（Event + outcome），把渲染请求交给
  调用方注入的 `on_request`（UI 负责调度回主线程），自己阻塞等待。

安全语义（与 PermissionManager 完全一致，fail-closed）：
- 无 UI / 回调抛错 / 超时 / 已关闭 → `UNAVAILABLE`（= 拒绝），绝不默认放行；
- `allowed_once` 只对当前请求有效，不做任何缓存或「记住选择」；
- `resolve` 首个结果生效，重复 / 过期请求返回 False（幂等且不可翻案）。
"""
from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Optional

from core.tools.permissions import ApprovalOutcome

log = logging.getLogger(__name__)

# 审批等待上限：超过即视为不可用（拒绝）。UI 挂起 / 用户离开不应永久卡住 worker。
DEFAULT_APPROVAL_TIMEOUT_S = 120.0

Resolver = Callable[[ApprovalOutcome], None]


@dataclass(frozen=True)
class ApprovalRequest:
    """一次待决审批（已脱敏到 UI 需要的最小信息集）。"""

    request_id: str
    tool_name: str
    reason: str
    args: dict
    created_at: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds")
    )


@dataclass
class _Slot:
    """待决槽：worker 线程等待，UI 线程填充。"""

    event: threading.Event = field(default_factory=threading.Event)
    outcome: ApprovalOutcome = ApprovalOutcome.UNAVAILABLE


class ApprovalBridge:
    """实现 `Approver` 协议的线程安全审批桥。

    用法（组合根）::

        bridge = ApprovalBridge(on_request=lambda req, resolve: page.run_task(
            show_dialog, req, resolve))
        runner = TaskRunner(agent=agent, stack=stack, approver=bridge)
    """

    def __init__(
        self,
        on_request: Callable[[ApprovalRequest, Resolver], None],
        *,
        timeout_s: float = DEFAULT_APPROVAL_TIMEOUT_S,
    ) -> None:
        self._on_request = on_request
        self._timeout_s = float(timeout_s)
        self._lock = threading.RLock()
        self._pending: Dict[str, _Slot] = {}
        self._closed = False

    # ----- Approver 协议 -----
    def ask(self, *, tool_name: str, reason: str, args: dict) -> ApprovalOutcome:
        """阻塞等待用户决策。任何异常路径都收敛为 UNAVAILABLE。"""
        request_id = uuid.uuid4().hex[:12]
        slot = _Slot()
        with self._lock:
            if self._closed:
                return ApprovalOutcome.UNAVAILABLE
            self._pending[request_id] = slot

        request = ApprovalRequest(
            request_id=request_id,
            tool_name=tool_name,
            reason=reason,
            args=dict(args or {}),
        )

        def _resolve_for_this(outcome: ApprovalOutcome) -> None:
            self.resolve(request_id, outcome)

        try:
            # on_request 在 worker 线程被调用；实现方必须自行调度回 UI 线程。
            self._on_request(request, _resolve_for_this)
        except Exception as exc:  # noqa: BLE001 - 回调异常绝不放行
            with self._lock:
                self._pending.pop(request_id, None)
            log.warning("审批请求投递失败（按拒绝处理）：%s", exc)
            return ApprovalOutcome.UNAVAILABLE

        if not slot.event.wait(self._timeout_s):
            with self._lock:
                self._pending.pop(request_id, None)
            log.warning("审批超时（%.0fs，按拒绝处理）：%s", self._timeout_s, tool_name)
            return ApprovalOutcome.UNAVAILABLE
        return slot.outcome

    # ----- UI 侧决策 -----
    def resolve(self, request_id: str, outcome: ApprovalOutcome) -> bool:
        """提交决策。首个结果生效；过期 / 重复 / 非法结果返回 False。"""
        if not isinstance(outcome, ApprovalOutcome):
            try:
                outcome = ApprovalOutcome(str(outcome))
            except ValueError:
                log.warning("非法审批结果（按拒绝处理）：%r", outcome)
                outcome = ApprovalOutcome.UNAVAILABLE
        with self._lock:
            slot = self._pending.pop(request_id, None)
        if slot is None:
            return False
        slot.outcome = outcome
        slot.event.set()
        return True

    def reject_all(self) -> int:
        """取消 Run / 关闭窗口：所有待决审批一律 REJECTED，唤醒 worker。"""
        with self._lock:
            slots = list(self._pending.values())
            self._pending.clear()
        for slot in slots:
            slot.outcome = ApprovalOutcome.REJECTED
            slot.event.set()
        return len(slots)

    def close(self) -> None:
        """永久关闭：之后所有 ask 直接 UNAVAILABLE（应用退出时调用）。"""
        with self._lock:
            self._closed = True
        self.reject_all()

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)
