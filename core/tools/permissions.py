"""PermissionManager：L0-L3 权限判定 + fail-closed 审批。

契约来源：INTERFACE_CONTRACTS_2.0.md §5 · TOOL_SYSTEM.md §4。
- outcome 封闭：allowed_once / rejected / unavailable；非 allowed_once 一律拒绝。
- 审批器缺失 / 抛错 / 返回未知 outcome = unavailable = 拒绝（绝不默认放行）。
- allowed_once 只对当前 tool_call 有效，不缓存授权。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Protocol, runtime_checkable

from .base import PermissionLevel, ToolSpec


class ApprovalOutcome(str, Enum):
    ALLOWED_ONCE = "allowed_once"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"  # 审批器缺失/异常/超时


class PermissionPolicyError(RuntimeError):
    """权限判定本身出错（如审批器抛异常）——Executor 收敛为拒绝，不冒泡。"""


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    outcome: ApprovalOutcome
    reason: str = ""


@runtime_checkable
class Approver(Protocol):
    """审批器接口（Phase 5 UI 实现；首版默认 None = L2 一律拒绝）。"""

    def ask(self, *, tool_name: str, reason: str, args: dict) -> ApprovalOutcome:
        ...


class PermissionManager:
    """权限判定器。L0/L1 直接放行；L2 走审批（fail-closed）；L3 永不放行。"""

    def check(
        self,
        spec: ToolSpec,
        args: dict,
        *,
        approver: Optional[Approver] = None,
    ) -> PermissionDecision:
        level = spec.permission
        if level is PermissionLevel.L3:
            return PermissionDecision(False, ApprovalOutcome.REJECTED, "L3 权限首版禁用")
        if level in (PermissionLevel.L0, PermissionLevel.L1):
            # L1 白名单 = 注册表内声明 L1 的工具（注册即授权，见 TOOL_SYSTEM §4）
            return PermissionDecision(True, ApprovalOutcome.ALLOWED_ONCE, "")
        # L2：必须审批，fail-closed
        if approver is None:
            return PermissionDecision(False, ApprovalOutcome.UNAVAILABLE,
                                       "L2 工具需要审批，但无可用审批器")
        try:
            outcome = approver.ask(tool_name=spec.name, reason=spec.description, args=args)
        except Exception as exc:  # noqa: BLE001 - 审批器异常绝不放行
            return PermissionDecision(False, ApprovalOutcome.UNAVAILABLE,
                                       f"审批器异常：{type(exc).__name__}")
        if outcome not in (ApprovalOutcome.ALLOWED_ONCE, ApprovalOutcome.REJECTED,
                           ApprovalOutcome.UNAVAILABLE):
            return PermissionDecision(False, ApprovalOutcome.UNAVAILABLE,
                                       f"未知审批结果：{outcome!r}")
        return PermissionDecision(
            outcome is ApprovalOutcome.ALLOWED_ONCE,
            outcome,
            "" if outcome is ApprovalOutcome.ALLOWED_ONCE else "用户拒绝或无审批器",
        )
