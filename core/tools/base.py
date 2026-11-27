"""Tool 系统的基础数据结构（2.x Phase 2）。

契约来源：docs/2x/INTERFACE_CONTRACTS_2.0.md §5。
- ToolSpec：工具自声明（name/description/parameters/permission/timeout/result_limit）。
- Tool Protocol：唯一能力接口；内置工具 / MCP 工具 / 外部包装都实现它。
- ToolResult：统一结果（ok/content/error_code/data），失败不抛裸异常。

设计纪律（继承三柱防注入）：发给模型的 schema 只含 name/description/parameters，
permission / timeout / execute 绝不出现在模型请求里（白名单导出，见 registry.schemas）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, runtime_checkable


class PermissionLevel(str, Enum):
    """L0-L3 权限分级。L3 首版禁止注册（任意 Shell/管理员）。"""

    L0 = "L0"   # 只读、无副作用：自动执行
    L1 = "L1"   # 低风险写入（白名单内）：自动执行
    L2 = "L2"   # 删除/修改用户数据：用户审批，fail-closed
    L3 = "L3"   # 任意系统命令/管理员：首版禁用


class ToolResultErrorCode(str, Enum):
    """ToolResult.error_code 的封闭集合（回注模型/审计用）。"""

    INVALID_ARGS = "invalid_args"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    UNKNOWN_TOOL = "unknown_tool"
    DUPLICATE_CALL = "duplicate_call"   # 幂等：同 tool_call_id 重复执行


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class ToolSpec:
    """工具自声明。构造时校验 name 格式与权限等级（程序级，不靠 Prompt）。"""

    name: str
    description: str
    parameters: dict                    # JSON Schema（object 根）
    permission: PermissionLevel
    timeout_s: float = 30.0
    result_limit: int = 4000

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(f"工具名非法（须 ^[a-z][a-z0-9_]{{0,63}}$）: {self.name!r}")
        if not self.description.strip():
            raise ValueError(f"工具描述不得为空: {self.name!r}")
        if self.parameters.get("type") != "object":
            raise ValueError(f"工具 parameters 根节点必须是 object: {self.name!r}")
        if not isinstance(self.permission, PermissionLevel):
            raise ValueError(f"权限等级必须是 PermissionLevel: {self.permission!r}")
        if self.permission is PermissionLevel.L3:
            raise ValueError("L3 权限首版禁止注册（任意 Shell/管理员操作）")
        if self.timeout_s <= 0:
            raise ValueError("timeout_s 必须为正数")
        if self.result_limit <= 0:
            raise ValueError("result_limit 必须为正数")

    def white_listed_schema(self) -> dict:
        """白名单导出：只给模型 name/description/parameters。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolResult:
    """统一结果。失败通过 error_code 表达，不向 Runtime 抛裸异常。"""

    ok: bool
    content: str                         # 回注模型的文本（已限长）
    error_code: Optional[str] = None     # ToolResultErrorCode 之一，或 None
    data: Optional[dict] = None          # 结构化产物（UI 用，不进模型）

    @classmethod
    def success(cls, content: str, *, data: Optional[dict] = None) -> "ToolResult":
        return cls(ok=True, content=content, data=data)

    @classmethod
    def fail(cls, error_code: str, message: str) -> "ToolResult":
        return cls(ok=False, content=message, error_code=error_code)


@runtime_checkable
class Tool(Protocol):
    """能力扩展唯一接口。实现者不得 import Runtime / UI（可独立单测）。"""

    spec: ToolSpec

    def execute(self, args: dict, *, cancel=None) -> ToolResult:
        ...
