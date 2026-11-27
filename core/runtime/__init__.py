"""Agent Runtime（2.x Phase 3）：Run 生命周期 + 多轮工具循环 + 事件流。

契约：docs/2x/AGENT_RUNTIME.md（设计冻结稿）+ INTERFACE_CONTRACTS_2.0.md §6。
红线：Runtime 只组织执行流程——不拼 Persona、不检索 Memory、不碰 SQLite 底层、
不决定长期记忆写入、不操作 Flet、不实现 DeepSeek 协议细节。
"""
from .state import RunStatus, RunStateMachine, InvalidTransition
from .events import AgentEvent, EventBus, EventType
from .store import RunRepository, RunRecord, StepRecord
from .engine import AgentRuntime, RunResult

__all__ = [
    "RunStatus", "RunStateMachine", "InvalidTransition",
    "AgentEvent", "EventBus", "EventType",
    "RunRepository", "RunRecord", "StepRecord",
    "AgentRuntime", "RunResult",
]
