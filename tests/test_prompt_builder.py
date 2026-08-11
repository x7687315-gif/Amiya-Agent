from core.persona import load_persona
from core.prompt_builder import PromptBuilder


def test_system_contains_persona_and_examples():
    p = load_persona()
    sys_p = PromptBuilder(p).build_system()
    assert "助手" in sys_p
    assert "用户" in sys_p
    assert "【世界观】" in sys_p
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
    sys_with = pb.build_system(block)
    assert "【相关用户记忆】" in sys_with
    assert "用户养了一只橘猫" in sys_with
    # 注入段落带使用约束，防止模型机械复述
    assert "不要生硬提及" in sys_with

