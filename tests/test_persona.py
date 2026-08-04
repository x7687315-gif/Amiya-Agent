import pytest

from core.persona import load_persona


def test_load_persona():
    p = load_persona()
    assert p.name == "助手"
    assert p.address == "用户"
    assert p.examples, "应有 few-shot 范例"
    assert any("user" in e and "assistant" in e for e in p.examples)
