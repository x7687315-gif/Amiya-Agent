"""轻量检索 trace：只看"有没有结果、结果好不好、快不快"，不存原文。

为什么要它：启动探针只能回答"嵌入能不能用"，回答不了"这一轮检索是否有效"。
没有 per-turn 数据，调阈值和判断 R2（reranker）是否有收益都只能靠感觉。

隐私边界（硬性）：
- **不记录 query 原文**，也不记录命中正文；只有通道名、状态、数量、分数、耗时。
- 进程内环形缓冲，默认 200 条，不落盘；重启即清空。
- 需要更详细诊断时，由调用方显式打开日志级别，而不是靠 trace 常驻。
"""
from __future__ import annotations

import threading
from collections import Counter, deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional

from .retrieval_status import RetrievalOutcome, RetrievalStatus


@dataclass(frozen=True)
class TraceEntry:
    channel: str                 # memory / knowledge
    status: RetrievalStatus
    hits: int
    candidates: int
    top_score: Optional[float]
    duration_ms: float
    reason: str = ""


class RetrievalTrace:
    """线程安全的环形缓冲（UI 线程与 worker 线程都会写）。"""

    def __init__(self, maxlen: int = 200) -> None:
        self._entries: Deque[TraceEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def record(self, channel: str, outcome: RetrievalOutcome) -> None:
        entry = TraceEntry(
            channel=channel,
            status=outcome.status,
            hits=len(outcome.hits),
            candidates=outcome.candidates,
            top_score=outcome.top_score,
            duration_ms=round(outcome.duration_ms, 1),
            reason=outcome.reason,
        )
        with self._lock:
            self._entries.append(entry)

    def snapshot(self) -> List[TraceEntry]:
        with self._lock:
            return list(self._entries)

    def summary(self) -> Dict[str, Dict[str, int]]:
        """按通道统计各状态出现次数（排查"是否经常降级"用）。"""
        with self._lock:
            entries = list(self._entries)
        out: Dict[str, Dict[str, int]] = {}
        for e in entries:
            bucket = out.setdefault(e.channel, {})
            bucket[e.status.value] = bucket.get(e.status.value, 0) + 1
        return out

    def degraded_ratio(self, channel: str) -> float:
        with self._lock:
            entries = [e for e in self._entries if e.channel == channel]
        if not entries:
            return 0.0
        bad = sum(1 for e in entries
                  if e.status in (RetrievalStatus.DEGRADED, RetrievalStatus.FAILED))
        return bad / len(entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


# 进程级默认实例：Agent 每轮写入，UI / 诊断脚本读
TRACE = RetrievalTrace()
