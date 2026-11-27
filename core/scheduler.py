"""TaskScheduler：到期任务提醒（2.x Phase 7 最小版）。

定位（与用户基线一致）：**LLM 不当定时器**。到点判断由本模块用系统时钟完成，
模型只负责"到点之后怎么说/要不要做事"。

设计取舍：
- **拉模式而非定时器堆**：每 N 秒查一次库，实现简单、重启即恢复、无需持久化
  定时器；代价是提醒精度为 N 秒（默认 30s），个人助理场景足够。
- **at-most-once**：`TaskRepository.claim_due()` 在同一把锁里"取走并打标"。
  崩溃最多漏一次提醒，不会重复打扰。
- **回调隔离**：单个回调异常只记日志，不影响同批其他任务，也不终止循环。
- **默认只提醒不自动执行**：`SCHEDULER_AUTORUN=0`，不产生任何 LLM 调用/费用；
  开启后才把任务交给 Runtime（这才用到 RequestRouter 的 internal_task 信号）。
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable, List, Optional

from core.tasks import Task, TaskRepository

log = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 30.0


class TaskScheduler:
    """到期任务轮询器（守护线程 + 可单测的同步 tick）。"""

    def __init__(
        self,
        repo: TaskRepository,
        on_due: Callable[[Task], None],
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        limit: int = 5,
        now_fn: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self._repo = repo
        self._on_due = on_due
        self._interval_s = max(1.0, float(interval_s))
        self._limit = max(1, int(limit))
        self._now = now_fn or datetime.now
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def interval_s(self) -> float:
        return self._interval_s

    # ----- 生命周期 -----
    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="amiya-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        self._thread = None

    def _loop(self) -> None:
        # 先 tick 再等：启动时就应把已过期的任务提醒出来，而不是等满一个周期
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 任何异常都不得杀死调度线程
                log.exception("调度 tick 失败（已忽略）")
            if self._stop.wait(self._interval_s):
                break

    # ----- 单次检查（同步，可单测） -----
    def tick(self) -> List[Task]:
        """取走到期任务并逐条回调。返回本次触发的任务（供测试/日志）。"""
        tasks = self._repo.claim_due(self._now(), limit=self._limit)
        for task in tasks:
            log.info("任务到期提醒：%s（due_at=%s）", task.title, task.due_at)
            try:
                self._on_due(task)
            except Exception:  # noqa: BLE001 - 单条回调失败不影响其余任务
                log.exception("到期回调失败（已忽略）：%s", task.task_id)
        return tasks
