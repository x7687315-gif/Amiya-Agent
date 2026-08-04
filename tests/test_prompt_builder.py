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
