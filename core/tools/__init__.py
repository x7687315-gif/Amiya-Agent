"""Tool System（2.x Phase 2）：能力扩展的唯一面。

设计基线：docs/2x/ARCHITECTURE_2.0.md §6 · INTERFACE_CONTRACTS_2.0.md §5
红线：模型参数不可信；权限在 Executor 内强制；首版禁 L3（任意 Shell/代码）。
"""
from .base import PermissionLevel, Tool, ToolResult, ToolSpec
from .registry import ToolRegistry
from .validator import SchemaValidator
from .executor import ToolExecutor
from .permissions import (
    ApprovalOutcome,
    PermissionDecision,
    PermissionManager,
    PermissionPolicyError,
)

__all__ = [
    "PermissionLevel", "Tool", "ToolResult", "ToolSpec",
    "ToolRegistry", "SchemaValidator", "ToolExecutor",
    "ApprovalOutcome", "PermissionDecision", "PermissionManager", "PermissionPolicyError",
]
