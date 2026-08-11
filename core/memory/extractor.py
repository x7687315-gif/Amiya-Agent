"""记忆抽取器 seam：自动抽取的记忆**只**能落候选闸门，绝不直写长期记忆正表。

Step A（Memory Gate）核心目标：
- 任何「系统建议记」的内容（含未来 Step 2.7 的 LLM 抽取）都经由本抽取器
  进入 `memory_candidate` 闸门，必须经人工确认（confirm）才转正入 `memory`。
- 本模块**不持有、不调用**任何直写 `memory` 正表的能力（中间层的 `remember`
  / 存储层的 `upsert_memory`）。数据隔离由「构造」保证：MemoryExtractor 只
  接收 `MemoryManager.propose` 这一条写路径，物理上无法越权写长期记忆。

分层与流向（绿色安全 / 橙色闸门）：

    Agent / 抽取器 → MemoryManager.propose → store.memory_candidate(pending)
                                                    │
                      人点「记住」→ confirm_candidate → memory（转正，可被检索）
                      人点「不用记」→ reject_candidate → 标记 rejected（永不进正表）

注意：本步只落地「闸门 + 抽取器 seam + 隔离测试」。真正把对话变成候选的
LLM 分类逻辑（调 DeepSeek、解析 JSON、产出 ExtractedMemory 列表）属于 Step 2.7，
不在本步范围。本模块提供 LLM-ready 的接口（propose_one / propose_many 接收
已抽取的 ExtractedMemory），分类器接入后只需把输出喂进来即可，无需改动闸门。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence

from .manager import MEMORY_TYPES, MemoryManager


@dataclass(frozen=True)
class ExtractedMemory:
    """一条被抽取出、等待人工确认的候选记忆。

    字段与 store.memory_candidate 对齐：type / content 必填，权重可选。
    type 必须是 MEMORY_TYPES 之一（fact/preference/event/goal/relationship）。
    """

    type: str
    content: str
    importance: int = 3
    confidence: int = 3
    reason: "str | None" = None
    source_msg_id: "int | None" = None

    def __post_init__(self) -> None:
        if self.type not in MEMORY_TYPES:
            raise ValueError(f"未知记忆类型: {self.type!r}，只接受 {MEMORY_TYPES}")


class MemoryExtractor:
    """抽取器 seam：把「已抽取的候选记忆」写入候选闸门，仅此一条写路径。

    职责边界（关键）：只写 memory_candidate，绝不写 memory 正表。
    - 不暴露任何直写长期记忆的方法；
    - 不接收、不保存中间层 `remember` 的引用（调用方若只把 propose 交来，
      物理上无法越权）。
    人工确认 / 否决仍由 MemoryManager（UI / API）负责——那是「人」的动作，
    不是「抽取器」的动作，故本类不提供 confirm / reject。
    """

    def __init__(self, memory: MemoryManager) -> None:
        # 只保存中间层引用；本类只用其中的 propose 这一条写路径。
        self._memory = memory

    # ----- 写路径（唯一：进候选闸门，绝不经 remember / upsert_memory） -----
    def propose_one(self, item: ExtractedMemory) -> int:
        """提交单条抽取候选，返回候选 id；被拒/空/黑名单/非法类型返回 0。

        落点是 memory_candidate（pending）——绝不会直接进 memory 正表，
        因此未经人工确认绝不污染检索与长期记忆。
        """
        return self._memory.propose(
            item.type,
            item.content,
            importance=item.importance,
            confidence=item.confidence,
            reason=item.reason,
            source_msg_id=item.source_msg_id,
        )

    def propose_many(self, items: Sequence[ExtractedMemory]) -> List[int]:
        """批量提交，返回每条对应的候选 id（顺序一致；无效项返回 0）。"""
        return [self.propose_one(it) for it in items]

    # ----- 只读视图（便于抽取器自查已有哪些待确认项，避免重复打扰） -----
    def pending(self, limit: int = 20) -> List[Dict[str, object]]:
        """查看当前待确认队列（只读，不写库）。"""
        return self._memory.pending_candidates(limit=limit)
