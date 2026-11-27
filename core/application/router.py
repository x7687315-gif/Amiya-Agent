"""RequestRouter + Handoff：Hybrid 双路径分流与上下文安全交接。

契约：INTERFACE_CONTRACTS_2.0.md §2 · ARCHITECTURE_2.0.md §3.2/§3.4。

分流信号（只用可观测事实，不猜语义、不关键词硬判）：
1. 用户显式任务意图（UI「执行任务」入口）；
2. 模型在快路径返回了有效 tool_calls；
3. 应用内部任务（Scheduler，未来 Phase 7）。
全部为否 → 快路径（现有 Agent.reply 逐字不动）。

注意：本 Router 是**策略层**，不持工具执行逻辑；真正的接管由组合根/UI 在
RUNTIME_ENABLED 灰度下完成（Phase 3C 只接线不翻转默认行为）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Handoff:
    """快路径 → Runtime 的上下文交接对象（架构 §3.4）。"""

    session_id: str
    system_prompt: str
    messages_snapshot: list[dict]   # 含已落库消息 + 已上屏 assistant 文本
    last_msg_id: int                # 幂等水位：之后的用户消息不重复落库
    trigger: str = "chat_tool"      # 写入 agent_run.trigger（与 DATA_MODEL §3.1 一致）


@dataclass(frozen=True)
class RouteDecision:
    path: str            # "fast" | "runtime"
    reason: str          # chat | explicit_task | model_tool_calls | internal_task
    tool_hint: str = ""


class RequestRouter:
    """确定性分流器：零依赖、零 I/O、可单测。"""

    def decide(
        self,
        *,
        explicit_task: bool = False,
        tool_calls: list | None = None,
        task_source: str | None = None,
        runtime_enabled: bool = False,
    ) -> RouteDecision:
        """按优先级判定路径。

        优先级：显式任务 > 内部任务 > 模型工具调用 > 快路径。
        runtime_enabled=False 时即使有工具调用也不接管（灰度总开关）——
        保持现有行为，避免在开关关闭时改变用户体验。
        """
        if explicit_task:
            return RouteDecision("runtime", "explicit_task")
        if task_source:
            return RouteDecision("runtime", "internal_task", tool_hint=task_source)
        if tool_calls:
            if runtime_enabled:
                return RouteDecision("runtime", "model_tool_calls")
            # 开关关：记录意图但走快路径（不丢失可观测性）
            return RouteDecision("fast", "model_tool_calls")
        return RouteDecision("fast", "chat")
