"""ContextAssembler：认知系统统一出口（2.x Phase 3A，行为等价抽出）。

设计基线：docs/2x/ARCHITECTURE_2.0.md §5 · INTERFACE_CONTRACTS_2.0.md §3。
来源：把 core/agent.py:218-260 的「检索 + 组装」段原样抽到这里，**不改九段顺序、
不改用词、不改降级语义**；用行为等价测试锁死。

红线：
- 只组装上下文，不调用工具、不请求 LLM、不碰 UI。
- 检索块只给内容不给分数（防注入）；status 只进 metadata（供 UI/日志），不进提示词。
- 记忆/知识检索失败只降级（返回空块 + 日志），不阻断对话——与原 reply 一致。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Dict, List, Optional

from core import emotion as _emotion
from core.memory.retrieval import RetrievalHit
from core.retrieval_trace import TRACE

if TYPE_CHECKING:  # 避免循环导入；运行时只依赖协议方法
    from core.knowledge.manager import KnowledgeManager
    from core.memory.manager import MemoryManager
    from core.persona.persona_manager import PersonaManager
    from core.prompt_builder import PromptBuilder

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssembledContext:
    """统一上下文对象：快路径与 Runtime 共用同一出口。

    - system_prompt：九段提示词（与原 build_system 输出逐字节一致）。
    - hits：记忆检索命中（供 Agent 的 on_retrieval 回调；不是给模型的）。
    - emotion：本轮情绪判定（供落库 _persist_emotion）。
    - metadata：检索来源/版本等非用户可见信息（不含置信度/候选状态）。
    """

    system_prompt: str
    messages: List[dict] = field(default_factory=list)  # 会话窗口；Runtime 路径使用
    hits: List[RetrievalHit] = field(default_factory=list)
    emotion: str = "calm"
    metadata: dict = field(default_factory=dict)


class ContextAssembler:
    """从 Agent 抽出的纯组装器。所有降级语义与原 reply 保持一致。"""

    def __init__(
        self,
        *,
        prompt_builder: "PromptBuilder",
        persona_manager: "PersonaManager",
        memory: "Optional[MemoryManager]" = None,
        memory_top_k: int = 5,
        knowledge: "Optional[KnowledgeManager]" = None,
        knowledge_top_k: int = 4,
    ) -> None:
        self._pb = prompt_builder
        self._pm = persona_manager
        self._memory = memory
        self._memory_top_k = memory_top_k
        self._knowledge = knowledge
        self._knowledge_top_k = knowledge_top_k

    def assemble(
        self,
        query: str,
        *,
        on_phase=None,
        now: Optional[datetime] = None,
        skills_block: str = "",
    ) -> AssembledContext:
        """组装一轮上下文。on_phase 保留原调用时机（retrieving → reasoning）。

        skills_block：技能目录（一级）。默认空串——快路径不传，保证九段提示词
        与 Phase 3A 抽出时逐字节一致；只有任务路径（有 load_skill 工具可调）才传。
        """
        # 记忆检索（原 agent.py:218-226）
        hits: List[RetrievalHit] = []
        mem_outcome = None
        if self._memory is not None:
            if on_phase is not None:
                on_phase("retrieving")
            try:
                mem_outcome = _retrieve_outcome(self._memory, query, self._memory_top_k)
                hits = list(mem_outcome.hits)
            except Exception as e:  # noqa: BLE001 - 检索失败只降级
                log.warning("记忆检索失败：%s", e)
            if mem_outcome is not None:
                TRACE.record("memory", mem_outcome)
        if on_phase is not None:
            on_phase("reasoning")

        # 记忆块（原 :232）
        memory_block = self._memory.format_block(hits) if self._memory is not None else ""

        # 知识块（原 :236-244）
        knowledge_block = ""
        kn_outcome = None
        if self._knowledge is not None:
            try:
                kn_outcome = _retrieve_outcome(self._knowledge, query, self._knowledge_top_k)
                khits = list(kn_outcome.hits)
            except Exception as e:  # noqa: BLE001
                log.warning("知识检索失败：%s", e)
                khits = []
            if kn_outcome is not None:
                TRACE.record("knowledge", kn_outcome)
            if khits:
                knowledge_block = self._knowledge.render_block(khits)

        # 情绪（原 :248）
        current_emotion = _emotion.detect_emotion(query)

        # 九段 system prompt（原 :251-260）——参数与顺序逐字保留
        relationship_block = self._pm.relationship_block()
        behavior_block = self._pm.behavior_block()
        system_prompt = self._pb.build_system(
            memory_block=memory_block,
            knowledge_block=knowledge_block,
            relationship_block=relationship_block,
            behavior_block=behavior_block,
            emotion_block=_emotion.emotion_block(current_emotion),
            skills_block=skills_block,
            now=now or datetime.now(),
        )

        return AssembledContext(
            system_prompt=system_prompt,
            hits=hits,
            emotion=current_emotion,
            metadata={
                "source": "context_assembler",
                "knowledge_used": bool(knowledge_block),
                # 检索状态（仅供 UI/日志：区分"没检索到"与"检索坏了"）
                "retrieval": {
                    "memory": _status_of(mem_outcome),
                    "knowledge": _status_of(kn_outcome),
                },
            },
        )


def _retrieve_outcome(manager, query: str, top_k: int):
    """优先走带状态的接口；老实现（或测试替身）只有 retrieve() 时降级包装。"""
    from core.retrieval_status import RetrievalOutcome, RetrievalStatus

    fn = getattr(manager, "retrieve_outcome", None)
    if callable(fn):
        # MagicMock 会自动生成任意属性，必须校验**返回值类型**才可靠
        out = fn(query, top_k=top_k)
        if isinstance(out, RetrievalOutcome):
            return out
    hits = manager.retrieve(query, top_k=top_k)
    return RetrievalOutcome(
        hits=hits,
        status=RetrievalStatus.SUCCESS if hits else RetrievalStatus.NO_RELEVANT_RESULTS,
        reason="" if hits else "below_threshold",
    )


def _status_of(outcome) -> dict:
    """把 RetrievalOutcome 压成可序列化的精简状态（不含正文）。"""
    if outcome is None:
        return {"status": "skipped", "hits": 0}
    return {
        "status": getattr(outcome.status, "value", str(outcome.status)),
        "hits": len(outcome.hits),
        "candidates": outcome.candidates,
        "duration_ms": round(outcome.duration_ms, 1),
        "reason": outcome.reason,
    }
