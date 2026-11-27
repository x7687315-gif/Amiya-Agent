"""出站消息序列校验：在请求发出前，把"结构不合法"的问题暴露出来。

为什么需要（2026-11-27 实机踩坑）：`_history[-40:]` 可能从一轮的 **assistant** 开始，
普通对话请求宽容（200），但带服务端工具的请求会 400
`an assistant message must follow a user message`——文档没写这条差异。
`Agent._window()` 已做归一化；本模块是**最后一道网**：任何调用方（含未来的新路径）
组装出非法序列时，都能在本地立刻发现，而不是等线上 400。

设计取舍：
- 只返回问题列表，不抛异常、不改消息——校验不能改变行为。
- 只覆盖两条**已验证**的硬约束（首条非 system 必须是 user；不能连续 assistant）。
  其余约束（tool 消息配对、reasoning_content 回传）尚未实测，不在这里假装校验。
"""
from __future__ import annotations

from typing import Dict, List, Sequence

# 问题代码（稳定字符串，便于日志聚合与测试断言）
FIRST_NOT_USER = "first_non_system_not_user"
CONSECUTIVE_ASSISTANT = "consecutive_assistant"
EMPTY_MESSAGES = "empty_messages"
UNKNOWN_ROLE = "unknown_role"

_KNOWN_ROLES = {"system", "user", "assistant", "tool"}


def validate_messages(messages: Sequence[Dict[str, object]]) -> List[str]:
    """返回问题代码列表；空列表表示通过。

    注意：**不评估内容质量**，只看结构。内容层面的问题（空串、超长）由调用方负责。
    """
    issues: List[str] = []
    if not messages:
        return [EMPTY_MESSAGES]

    first_non_system = next(
        (m for m in messages if str(m.get("role")) != "system"), None)
    if first_non_system is None or str(first_non_system.get("role")) != "user":
        # 只有 system、或首条非 system 不是 user，都会被服务端拒绝
        issues.append(FIRST_NOT_USER)

    prev: str = ""
    for m in messages:
        role = str(m.get("role") or "")
        if role not in _KNOWN_ROLES:
            issues.append(UNKNOWN_ROLE)
        if role == "assistant" and prev == "assistant":
            issues.append(CONSECUTIVE_ASSISTANT)
        prev = role

    # 去重但保持顺序（同一问题不重复报）
    seen: set[str] = set()
    uniq = [x for x in issues if not (x in seen or seen.add(x))]
    return uniq
