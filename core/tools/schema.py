"""JSON Schema 子集校验器（纯标准库，零新增依赖）。

覆盖首批工具所需：type / required / enum / minimum / maximum /
minLength / maxLength / items / additionalProperties:false。
不支持的 schema 关键字一律忽略（前向兼容），不当作错误。

契约来源：INTERFACE_CONTRACTS_2.0.md §5 · TOOL_SYSTEM.md §3。
"""
from __future__ import annotations

from typing import Any

_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


def _check_type(value: Any, expected: str) -> bool:
    py = _TYPE_MAP.get(expected)
    if py is None:
        return True  # 未知 type 不阻断（前向兼容）
    if expected == "number" and isinstance(value, bool):
        return False  # bool 是 int 子类，显式排除
    if expected == "integer" and isinstance(value, bool):
        return False
    return isinstance(value, py)


def validate(value: Any, schema: dict, path: str = "$") -> None:
    """就地校验；违反约束抛 ValueError（含路径，便于回注模型修正）。"""
    expected = schema.get("type")
    if expected and not _check_type(value, expected):
        raise ValueError(f"{path}: 类型应为 {expected}，实际 {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: 不在允许值 {schema['enum']} 内")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path}: 小于最小值 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path}: 超过最大值 {schema['maximum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{path}: 长度不足 {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path}: 长度超过 {schema['maxLength']}")

    if isinstance(value, dict):
        props: dict = schema.get("properties") or {}
        for req in schema.get("required") or []:
            if req not in value:
                raise ValueError(f"{path}.{req}: 缺少必填字段")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                raise ValueError(f"{path}: 不允许的额外字段 {sorted(extra)}")
        for key, sub in value.items():
            if key in props:
                validate(sub, props[key], f"{path}.{key}")

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")
