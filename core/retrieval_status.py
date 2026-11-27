"""检索结果的状态模型：把"没有结果"和"检索坏了"分开。

背景（2026-11-27）：`retrieve()` 在三种情况下都返回 `[]`——
① 语料里确实没有相关内容；② 相关性闸门把候选全挡了；③ 嵌入模型/索引坏了。
调用方无法区分，界面上都是"没有参考"，于是故障可以潜伏很久（向量路断了几天才发现）。

状态语义（刻意只有四个，不追求更细）：
- SUCCESS：正常检索，且有命中；
- NO_RELEVANT_RESULTS：链路正常，但没有内容达到相关性门槛（**这是正常结果**）；
- DEGRADED：链路部分可用（如嵌入不可用，只剩关键词路），结果可能不完整；
- FAILED：检索本身出错，拿不到结果。

约定：**DEGRADED 仍可携带 hits**（部分可用比全丢好），FAILED 一定没有 hits。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional


class RetrievalStatus(str, Enum):
    SUCCESS = "success"
    NO_RELEVANT_RESULTS = "no_relevant_results"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass(frozen=True)
class RetrievalOutcome:
    """一次检索的完整结果（命中 + 状态 + 可观测数字）。

    刻意**不含 query 原文**：trace 可能被写入日志或展示，原文属于用户隐私。
    """

    hits: List[Any] = field(default_factory=list)
    status: RetrievalStatus = RetrievalStatus.NO_RELEVANT_RESULTS
    reason: str = ""
    candidates: int = 0
    top_score: Optional[float] = None
    duration_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in (RetrievalStatus.SUCCESS, RetrievalStatus.DEGRADED)

    @property
    def degraded(self) -> bool:
        return self.status is RetrievalStatus.DEGRADED

    def describe(self) -> str:
        """一行可读摘要（日志/UI 用，不含正文）。"""
        bits = [self.status.value]
        if self.reason:
            bits.append(f"reason={self.reason}")
        bits.append(f"hits={len(self.hits)}")
        if self.top_score is not None:
            bits.append(f"top={self.top_score:.3f}")
        bits.append(f"{self.duration_ms:.0f}ms")
        return " ".join(bits)
