"""Application Facade：组合根装配 Helper（2.x Phase 3C）。

目的：把 Runtime 栈的构造集中到 application 层，避免 ui/app.py 组合根继续膨胀
（docs/23 指出组合根是全仓库杠杆最高的重构点）。本工厂只装配、不启动任何任务。

灰度纪律：调用方（UI）在 RUNTIME_ENABLED=False 时不使用 runtime；
快路径（Agent.reply）逐字不动。本函数不改变任何现有对象。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from core.memory.store import SQLiteMemoryStore
from core.runtime import AgentRuntime, EventBus, RunRepository
from core.skills import SkillTool
from core.tasks import TaskRepository
from core.tools import ToolExecutor, ToolRegistry
from core.tools.builtin import CalculatorTool, TaskDeleteTool, TaskTool, TimeTool

from .router import RequestRouter

log = logging.getLogger(__name__)


@dataclass
class RuntimeStack:
    """装配好的行动系统（供显式任务入口 / 未来 UI 使用）。"""

    router: RequestRouter
    registry: ToolRegistry
    executor: ToolExecutor
    repo: RunRepository
    bus: EventBus
    runtime: AgentRuntime
    tasks: TaskRepository


def build_runtime_stack(
    *,
    llm,
    store: SQLiteMemoryStore,
    task_repo: Optional[TaskRepository] = None,
    skills=None,
) -> RuntimeStack:
    """构造行动系统全栈：注册表 → 工具 → 执行器 → RunRepository → Runtime。

    - 零网络、零 I/O（除 SQLite 表已由 v5 迁移建好）。
    - 任何一步失败由调用方捕获降级——本函数不吞异常（组合根决定降级策略）。
    - skills 非空时额外注册 `load_skill`（L0）；技能正文按需加载，不常驻提示词。
    """
    registry = ToolRegistry()
    tasks = task_repo or TaskRepository(store)
    # 注册顺序固定：L0 → L1 → L2（确定性好测）
    registry.register(TimeTool())
    registry.register(CalculatorTool())
    registry.register(TaskTool(tasks))
    registry.register(TaskDeleteTool(tasks))
    if skills:
        registry.register(SkillTool(skills))
    executor = ToolExecutor(registry)
    repo = RunRepository(store)
    bus = EventBus()
    runtime = AgentRuntime(llm=llm, executor=executor, repo=repo, bus=bus)
    return RuntimeStack(
        router=RequestRouter(), registry=registry, executor=executor,
        repo=repo, bus=bus, runtime=runtime, tasks=tasks,
    )
