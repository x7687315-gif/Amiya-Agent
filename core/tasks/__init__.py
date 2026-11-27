"""Task 持久化（2.x Phase 2B）：assistant_task 表仓储。

与 Memory 逻辑隔离：任务完成只改状态，不写 memory/candidate。
契约见 docs/2x/INTERFACE_CONTRACTS_2.0.md §7。
"""
from .repository import Task, TaskRepository

__all__ = ["Task", "TaskRepository"]
