"""参数校验入口：模型输出 = 不可信输入。

失败返回 ToolResult.fail(invalid_args) 而非抛异常（由 Executor 调用）。
契约来源：INTERFACE_CONTRACTS_2.0.md §5 · TOOL_SYSTEM.md §3。
"""
from __future__ import annotations

from .base import ToolResult, ToolResultErrorCode
from .schema import validate as _validate_schema


class SchemaValidator:
    """无状态校验器；可对任意 ToolSpec.parameters 校验 args。"""

    def validate(self, schema: dict, args: dict) -> tuple[bool, str]:
        try:
            if not isinstance(args, dict):
                raise ValueError("参数必须是 JSON 对象")
            _validate_schema(args, schema)
            return True, ""
        except ValueError as exc:
            return False, str(exc)

    def validate_or_result(self, schema: dict, args: dict) -> ToolResult | None:
        """校验通过返回 None；失败返回可回注模型的 ToolResult。"""
        ok, msg = self.validate(schema, args)
        if ok:
            return None
        return ToolResult.fail(
            ToolResultErrorCode.INVALID_ARGS.value,
            f"参数校验失败：{msg}。请修正后重试。",
        )
