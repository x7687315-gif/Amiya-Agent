from core.persona import load_persona
from core.prompt_builder import PromptBuilder


def test_system_contains_persona_and_examples():
    p = load_persona()
    sys_p = PromptBuilder(p).build_system()
    assert "助手" in sys_p
    assert "用户" in sys_p
    assert "【助手的视角与价值观】" in sys_p
    # few-shot 范例应进入系统提示词，强化语感
    assert "用户：用户，今天辛苦了。" in sys_p
    # 稳定性护盾
    assert "不要脱离助手" in sys_p


def test_build_system_injects_memory_block():
    """Step 2.4 Prompt 注入：检索命中应被【相关用户记忆】定界包裹进系统提示词。"""
    pb = PromptBuilder(load_persona())
    sys_no = pb.build_system()
    assert "【相关用户记忆】" not in sys_no  # 无记忆时不注入

    block = "- (事实) 用户养了一只橘猫"
    sys_with = pb.build_system(memory_block=block)
    assert "【相关用户记忆】" in sys_with
    assert "用户养了一只橘猫" in sys_with
    # 注入段落带使用约束，防止模型机械复述
    assert "不要生硬提及" in sys_with


def test_build_system_injection_order():
    """Phase 3 注入顺序：身份 < 价值观 < 知识 < 记忆 < 关系 < 情绪 < 行为 < 语言。

    人格先于上下文，减少角色漂移；三柱仍各自独立、互不混入。
    """
    pb = PromptBuilder(load_persona())
    sys_p = pb.build_system(
        knowledge_block="【角色知识】\n- (rel.md) 助手信任用户",
        memory_block="- (事实) 用户养了一只橘猫",
        relationship_block="【与用户的关系】\n- 当前关系阶段：信任",
        behavior_block="【行为准则】\n- 始终以用户称呼",
        emotion_block="【当前情绪】平静而专注",
    )
    i_identity = sys_p.index("你是")
    i_values = sys_p.index("【助手的视角与价值观】")
    i_knowledge = sys_p.index("【角色知识】")
    i_memory = sys_p.index("【相关用户记忆】")
    i_relationship = sys_p.index("【与用户的关系】")
    i_emotion = sys_p.index("【当前情绪】")
    i_behavior = sys_p.index("【行为准则】")
    i_speech = sys_p.index("【语言风格】")
    assert (
        i_identity
        < i_values
        < i_knowledge
        < i_memory
        < i_relationship
        < i_emotion
        < i_behavior
        < i_speech
    )
    # 分数绝不进提示词
    assert "score=" not in sys_p

