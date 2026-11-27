"""Run 状态机（封闭枚举 + 显式迁移表，非法迁移抛错不静默）。

契约：docs/2x/AGENT_RUNTIME.md §2 · ARCHITECTURE_2.0.md §7.1。
状态：created/running/waiting_approval/completed/failed/cancelled；终态不可逆。
"""
from __future__ import annotations

from enum import Enum


class RunStatus(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})

# 允许的迁移（源 → 目标集合）
_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset({
        RunStatus.RUNNING, RunStatus.WAITING_APPROVAL,
        RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED,
    }),
    RunStatus.WAITING_APPROVAL: frozenset({
        RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED,
    }),
    # 终态：无出边（不可逆）
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class InvalidTransition(RuntimeError):
    def __init__(self, current: RunStatus, target: RunStatus) -> None:
        super().__init__(f"非法 Run 状态迁移：{current.value} → {target.value}")


class RunStateMachine:
    """无状态迁移校验器（状态存在 RunRecord 里，这里只判定合法性）。"""

    @staticmethod
    def can(current: RunStatus, target: RunStatus) -> bool:
        return target in _TRANSITIONS.get(current, frozenset())

    @staticmethod
    def require(current: RunStatus, target: RunStatus) -> None:
        if not RunStateMachine.can(current, target):
            raise InvalidTransition(current, target)

    @staticmethod
    def is_terminal(status: RunStatus) -> bool:
        return status in TERMINAL
