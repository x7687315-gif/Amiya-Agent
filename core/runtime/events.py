"""AgentEvent + EventBus：唯一观察面（dataclass + 回调列表，非中间件）。

契约：ARCHITECTURE_2.0.md §7.2 · INTERFACE_CONTRACTS_2.0.md §6。
红线：Event Bus 不充当业务数据库；只广播真实携带的状态（显示精度 ≤ 接口精度）；
任一 subscriber 抛错只记日志，不影响 Run（沿用现有回调异常吞噬约定）。
"""
from __future__ import annotations

import itertools
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List

log = logging.getLogger(__name__)


class EventType:
    """事件类型常量（str，封闭集合，便于 UI 穷举渲染）。"""

    RUN_STARTED = "run_started"
    THINKING_STARTED = "thinking_started"
    TOOL_REQUESTED = "tool_requested"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DECIDED = "approval_decided"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    RESPONSE_DELTA = "response_delta"
    RESPONSE_COMPLETED = "response_completed"
    RUN_CANCELLED = "run_cancelled"
    RUN_FAILED = "run_failed"
    RUN_COMPLETED = "run_completed"


@dataclass(frozen=True)
class AgentEvent:
    run_id: str
    type: str
    seq: int
    ts: str
    data: dict = field(default_factory=dict)

    @classmethod
    def create(cls, run_id: str, event_type: str, seq: int, data: dict | None = None) -> "AgentEvent":
        return cls(run_id, event_type, seq,
                   datetime.now().isoformat(timespec="milliseconds"), data or {})


class EventBus:
    """极简单播总线：subscribe 返回 disposer；publish 同步派发给所有订阅者。"""

    def __init__(self) -> None:
        self._subs: List[Callable[[AgentEvent], None]] = []
        self._seq = itertools.count(1)
        self._lock = threading.RLock()
        self._by_run: dict[str, int] = {}

    def subscribe(self, callback: Callable[[AgentEvent], None]) -> Callable[[], None]:
        with self._lock:
            self._subs.append(callback)

        def dispose() -> None:
            with self._lock:
                if callback in self._subs:
                    self._subs.remove(callback)

        return dispose

    def emit(self, run_id: str, event_type: str, data: dict | None = None) -> AgentEvent:
        """为 run 生成递增 seq 的事件并同步派发；订阅者异常只记日志。"""
        with self._lock:
            seq = self._by_run.get(run_id, 0) + 1
            self._by_run[run_id] = seq
            subs = list(self._subs)
        event = AgentEvent.create(run_id, event_type, seq, data)
        for sub in subs:
            try:
                sub(event)
            except Exception as e:  # noqa: BLE001 - 单一订阅者异常不影响 Run
                log.warning("AgentEvent 订阅者异常（type=%s）：%s", event_type, e)
        return event

    def next_seq(self, run_id: str) -> int:
        with self._lock:
            return self._by_run.get(run_id, 0) + 1
