"""Phase 3A 行为等价测试：ContextAssembler 抽出后与原 reply 组装段逐字节一致。

等价锁死三项：
1. system_prompt 字节级一致（同依赖、同 query、同 now）。
2. hits / emotion 一致。
3. on_phase 调用序列一致（retrieving → reasoning），检索失败降级语义一致。
"""
from datetime import datetime
from unittest.mock import MagicMock

from core import emotion as _emotion
from core.context import AssembledContext, ContextAssembler
from core.memory.retrieval import RetrievalHit, Weights
from core.persona import Persona, PersonaManager
from core.prompt_builder import PromptBuilder


def _persona_and_pb():
    persona = Persona(
        name="助手", title="本地",
        identity={"perspective": ["测试"], "values": ["测试"], "thinking_style": "冷静"},
        speech={"tone": "温柔", "address_user": "用户"},
    )
    return persona, PromptBuilder(persona), PersonaManager(persona)


def _fake_hits():
    return [RetrievalHit(
        id=1, type="fact", content="测试记忆", importance=3, confidence=3,
        score=0.8, channels={"vector": 0.8},
    )]


def _legacy_assemble(query, *, pb, pm, memory, memory_top_k, knowledge, knowledge_top_k,
                     on_phase, now):
    """内联复刻原 agent.py:218-260 的组装逻辑（抽出前的逐字等价参照）。"""
    hits = []
    if memory is not None:
        if on_phase is not None:
            on_phase("retrieving")
        try:
            hits = memory.retrieve(query, top_k=memory_top_k)
        except Exception:
            hits = []
    if on_phase is not None:
        on_phase("reasoning")
    memory_block = memory.format_block(hits) if memory is not None else ""
    knowledge_block = ""
    if knowledge is not None:
        try:
            khits = knowledge.retrieve(query, top_k=knowledge_top_k)
        except Exception:
            khits = []
        if khits:
            knowledge_block = knowledge.render_block(khits)
    current_emotion = _emotion.detect_emotion(query)
    relationship_block = pm.relationship_block()
    behavior_block = pm.behavior_block()
    system = pb.build_system(
        memory_block=memory_block, knowledge_block=knowledge_block,
        relationship_block=relationship_block, behavior_block=behavior_block,
        emotion_block=_emotion.emotion_block(current_emotion), now=now,
    )
    return system, hits, current_emotion


def test_system_prompt_byte_equivalent():
    persona, pb, pm = _persona_and_pb()
    memory = MagicMock()
    memory.retrieve.return_value = _fake_hits()
    memory.format_block.return_value = "【记忆块】测试"
    knowledge = MagicMock()
    knowledge.retrieve.return_value = []
    now = datetime(2026, 11, 23, 12, 0, 0)
    query = "用户今天开心吗？"

    phases_new = []
    assembler = ContextAssembler(
        prompt_builder=pb, persona_manager=pm,
        memory=memory, memory_top_k=5, knowledge=knowledge, knowledge_top_k=4,
    )
    ctx = assembler.assemble(query, on_phase=phases_new.append, now=now)

    phases_old = []
    old_system, old_hits, old_emotion = _legacy_assemble(
        query, pb=pb, pm=pm, memory=memory, memory_top_k=5,
        knowledge=knowledge, knowledge_top_k=4, on_phase=phases_old.append, now=now,
    )
    assert ctx.system_prompt == old_system
    assert ctx.hits == old_hits
    assert ctx.emotion == old_emotion
    assert phases_new == phases_old == ["retrieving", "reasoning"]


def test_no_memory_knowledge_equivalent():
    """无记忆/知识时行为等价（纯内存模式）。"""
    persona, pb, pm = _persona_and_pb()
    now = datetime(2026, 11, 23, 12, 0, 0)
    phases = []
    assembler = ContextAssembler(prompt_builder=pb, persona_manager=pm)
    ctx = assembler.assemble("你好", on_phase=phases.append, now=now)
    old_system, _, _ = _legacy_assemble(
        "你好", pb=pb, pm=pm, memory=None, memory_top_k=5,
        knowledge=None, knowledge_top_k=4, on_phase=None, now=now,
    )
    assert ctx.system_prompt == old_system
    assert phases == ["reasoning"]  # 无记忆时不发 retrieving（与原逻辑一致）
    assert ctx.hits == []
    assert isinstance(ctx, AssembledContext)


def test_retrieval_failure_degrades_equivalent():
    """记忆/知识检索抛异常时只降级，不阻断——与原 reply 一致。"""
    persona, pb, pm = _persona_and_pb()
    memory = MagicMock()
    memory.retrieve.side_effect = RuntimeError("db gone")
    memory.format_block.return_value = ""
    knowledge = MagicMock()
    knowledge.retrieve.side_effect = RuntimeError("kb gone")
    now = datetime(2026, 11, 23, 12, 0, 0)
    assembler = ContextAssembler(
        prompt_builder=pb, persona_manager=pm,
        memory=memory, knowledge=knowledge,
    )
    ctx = assembler.assemble("测试", on_phase=lambda _: None, now=now)
    assert ctx.hits == []
    assert ctx.metadata["knowledge_used"] is False
    assert ctx.system_prompt  # 仍有完整九段提示词


def test_metadata_no_internal_scores():
    """metadata 不含置信度/候选状态（防注入与信息最小化）。"""
    persona, pb, pm = _persona_and_pb()
    assembler = ContextAssembler(prompt_builder=pb, persona_manager=pm)
    ctx = assembler.assemble("x", now=datetime(2026, 1, 1))
    assert "confidence" not in ctx.metadata
    assert "candidate" not in ctx.metadata
    # 新增 retrieval 状态（无分数、无正文，仅用于区分"没检索到"与"检索坏了"）
    assert set(ctx.metadata) == {"source", "knowledge_used", "retrieval"}
    for ch in ctx.metadata["retrieval"].values():
        assert set(ch) <= {"status", "hits", "candidates", "duration_ms", "reason"}
