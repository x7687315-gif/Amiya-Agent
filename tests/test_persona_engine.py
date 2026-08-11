"""Persona 子系统单测：包导入兼容、worldview->perspective、Relationship stage 映射、
PersonaManager 块拼装、Agent 接线注入关系/行为块。

不进入 Emotion / Voice 实现（仅验证 seam 存在、不阻断对话）。
"""
from __future__ import annotations

import pytest

from core.agent import Agent
from core.memory import MemoryManager, SQLiteMemoryStore
from core.persona import Persona, PersonaManager, load_persona
from core.persona.behavior_rules import BehaviorRules
from core.persona.relationship import RelationshipManager


# ----- 兼容性：旧 import 不能断 -----
def test_old_imports_still_work():
    assert Persona is not None
    p = load_persona()
    assert p.name == "助手"
    assert p.address == "用户"


def test_persona_manager_importable():
    assert PersonaManager is not None


# ----- worldview -> perspective -----
def test_worldview_renamed_to_perspective():
    idn = load_persona().identity
    assert "perspective" in idn
    assert "worldview" not in idn
    assert "relationship_to_doctor" not in idn  # 已迁移到 relationship.yaml
    assert "behavior_guidelines" not in idn  # 已迁移到 behavior.yaml


# ----- RelationshipManager：stage 映射 -----
def test_relationship_stage_mapping():
    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    rm = RelationshipManager(memory=mgr)
    # 默认 trust=70 -> 信任
    assert rm.trust == 70
    assert rm.stage == "信任"

    rm.set_trust(20)
    assert rm.trust == 20
    assert rm.stage == "初识"

    rm.set_trust(45)
    assert rm.stage == "熟悉"

    rm.set_trust(70)
    assert rm.stage == "信任"

    rm.set_trust(90)
    assert rm.stage == "深度陪伴"


def test_relationship_block_no_tone_control():
    store = SQLiteMemoryStore(":memory:")
    rm = RelationshipManager(memory=MemoryManager(store))
    blk = rm.block()
    assert blk is not None
    assert "【与用户的关系】" in blk
    assert "信任" in blk
    # stage 只作背景事实，明确不控语气
    assert "不要因此改变说话方式" in blk


def test_relationship_block_none_without_memory():
    rm = RelationshipManager(memory=None)
    assert rm.block() is None


# ----- BehaviorRules / PersonaManager 块拼装 -----
def test_behavior_rules_block():
    br = BehaviorRules()
    blk = br.block()
    assert "【行为准则】" in blk
    assert "用户" in blk  # 来自 behavior.yaml


def test_persona_manager_blocks():
    store = SQLiteMemoryStore(":memory:")
    pm = PersonaManager(memory=MemoryManager(store))
    bb = pm.behavior_block()
    assert bb is not None
    assert "【行为准则】" in bb
    assert "【知识边界】" in bb  # knowledge_scope 并入行为块

    rb = pm.relationship_block()
    assert rb is not None
    assert "【与用户的关系】" in rb

    vb = pm.values_block()
    assert "【助手的视角与价值观】" in vb
    assert "perspective" not in vb  # 不应泄露字段名


def test_persona_manager_without_memory_skips_relationship():
    pm = PersonaManager(memory=None)
    assert pm.relationship_block() is None
    # 行为块来自 YAML，与 DB 无关，仍应有
    assert pm.behavior_block() is not None


# ----- Agent 接线：每轮注入关系/行为块，且不阻断对话 -----
class _CaptureLLM:
    def __init__(self, reply="用户，我在哦。"):
        self.reply = reply
        self.last_system = ""

    def stream_chat(self, system, history):
        self.last_system = system
        yield self.reply


def test_agent_injects_relationship_and_behavior_blocks():
    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    llm = _CaptureLLM()
    agent = Agent(persona=load_persona(), llm=llm, memory=mgr)
    "".join(agent.reply("用户，今天辛苦了。"))

    sys_p = llm.last_system
    # 关系块（动态 persona_state）与行为块都已注入
    assert "【与用户的关系】" in sys_p
    assert "【行为准则】" in sys_p
    # 三柱隔离仍成立：记忆表为空时不应出现记忆块
    assert "【相关用户记忆】" not in sys_p
    # 人格先于上下文：身份锚定在关系/行为之前
    assert sys_p.index("你是") < sys_p.index("【与用户的关系】")


def test_agent_works_without_memory_no_relationship_block():
    llm = _CaptureLLM()
    agent = Agent(persona=load_persona(), llm=llm)  # memory=None
    "".join(agent.reply("你好"))
    # 无记忆时不应注入关系块（persona_state 不可用）
    assert "【与用户的关系】" not in llm.last_system
    # 行为/语言/价值观仍注入（来自 YAML）
    assert "【行为准则】" in llm.last_system
    assert "【助手的视角与价值观】" in llm.last_system
