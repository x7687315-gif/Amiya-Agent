"""CalculatorTool（L0）：AST 白名单求值，绝不 eval/exec。

安全红线（TOOL_SYSTEM.md §5）：
- 只允许数字 / + - * / % // ** / 括号 / 一元正负 / 白名单 math 函数。
- 任何 Name / Call（白名单外）/ Attribute / 比较 / 布尔运算节点一律拒绝。
- 表达式 ≤200 字符、结果 |x|<1e15、幂指数 ≤100（防 CPU 炸弹）、除零明确报错。
"""
from __future__ import annotations

import ast
import math

from ..base import PermissionLevel, ToolResult, ToolSpec

_MAX_EXPR = 200
_MAX_RESULT = 1e15
_MAX_POW = 100
_SAFE_FUNCS = {
    "abs": abs, "round": round,
    "sqrt": math.sqrt, "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.FloorDiv, ast.Pow,
    ast.USub, ast.UAdd, ast.Call, ast.Name, ast.Load, ast.Tuple,
)


class CalculatorTool:
    spec = ToolSpec(
        name="calculate",
        description="安全计算数学表达式，支持 + - * / % // ** 与 abs/round/sqrt/floor/ceil/log/exp/sin/cos/tan。不支持变量或函数调用（白名单外）。",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "minLength": 1, "maxLength": _MAX_EXPR}
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        permission=PermissionLevel.L0,
        timeout_s=5.0,
    )

    def execute(self, args: dict, *, cancel=None) -> ToolResult:
        expr = args.get("expression") or ""
        if not isinstance(expr, str) or len(expr) > _MAX_EXPR:
            return ToolResult.fail("invalid_args", "表达式非法或超过 200 字符")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError:
            return ToolResult.fail("invalid_args", "表达式语法错误")
        # 1. 节点白名单
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                if node.id not in _SAFE_FUNCS:
                    return ToolResult.fail("invalid_args", f"不允许的标识符：{node.id!r}")
            elif isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
                    return ToolResult.fail("invalid_args", "不允许的函数调用")
                if node.keywords:
                    return ToolResult.fail("invalid_args", "不支持关键字参数")
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
                # 2. 幂指数上限（静态检查右操作数）
                if isinstance(node.right, ast.Constant) and isinstance(node.right.value, (int, float)):
                    if abs(node.right.value) > _MAX_POW:
                        return ToolResult.fail("invalid_args", "幂指数超过 100")
            elif not isinstance(node, _ALLOWED_NODES):
                return ToolResult.fail("invalid_args",
                                       f"不允许的语法节点：{type(node).__name__}")
        try:
            value = eval(compile(tree, "<calc>", "eval"),  # noqa: S307 - AST 已白名单过滤
                         {"__builtins__": {}}, dict(_SAFE_FUNCS))
        except ZeroDivisionError:
            return ToolResult.fail("execution_error", "除数不能为零")
        except (ValueError, OverflowError) as exc:
            return ToolResult.fail("execution_error", f"计算错误：{exc}")
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail("execution_error", f"计算失败：{type(exc).__name__}")
        if isinstance(value, complex) or not isinstance(value, (int, float)):
            return ToolResult.fail("execution_error", "结果不是实数")
        if abs(value) >= _MAX_RESULT:
            return ToolResult.fail("execution_error", "结果绝对值超过 1e15")
        text = str(int(value)) if float(value).is_integer() else f"{value:.10g}"
        return ToolResult.success(text, data={"value": value})
