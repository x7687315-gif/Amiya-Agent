"""Phase 6 测试：SKILL.md 解析 + 目录注入 + load_skill 工具 + 提示词零变化。"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.agent import Agent
from core.application import build_runtime_stack
from core.memory.store import SQLiteMemoryStore
from core.persona import load_persona
from core.skills import SkillRegistry, SkillTool, load_skills, parse_skill_md
from core.skills.loader import MAX_BODY_CHARS


VALID = """---
name: weekly-review
description: 每周回顾
when_to_use: 用户说周回顾时
tools: [get_time, manage_task]
---

# 每周回顾

1. 查时间
2. 列任务
"""


# ---------------------------------------------------------------- 解析

def test_parse_valid_frontmatter():
    skill = parse_skill_md(VALID, fallback_name="dir")
    assert skill is not None
    assert skill.name == "weekly-review"
    assert skill.description == "每周回顾"
    assert skill.when_to_use == "用户说周回顾时"
    assert skill.tools == ("get_time", "manage_task")
    assert "1. 查时间" in skill.body
    assert "---" not in skill.body  # 分隔符不进正文


def test_parse_rejects_missing_description():
    text = "---\nname: x\n---\nbody"
    assert parse_skill_md(text, fallback_name="x") is None


def test_parse_rejects_bad_name():
    text = "---\nname: Weekly Review\ndescription: d\n---\nbody"
    assert parse_skill_md(text, fallback_name="dir") is None
    text2 = "---\nname: 周回顾\ndescription: d\n---\nbody"
    assert parse_skill_md(text2, fallback_name="dir") is None


def test_parse_rejects_unclosed_frontmatter():
    assert parse_skill_md("---\nname: x\ndescription: d\nbody", fallback_name="x") is None


def test_parse_without_frontmatter_uses_dir_name_but_needs_description():
    # 没有 frontmatter 就取不到 description → 按无效处理（宁可跳过，不猜）
    assert parse_skill_md("just body", fallback_name="plain") is None


def test_parse_truncates_long_body():
    text = f"---\nname: big\ndescription: d\n---\n" + ("x" * (MAX_BODY_CHARS + 100))
    skill = parse_skill_md(text, fallback_name="big")
    assert skill is not None
    assert len(skill.body) <= MAX_BODY_CHARS + 20
    assert "已截断" in skill.body


def test_parse_empty_text():
    assert parse_skill_md("", fallback_name="x") is None


# ---------------------------------------------------------------- 目录扫描

def test_load_skills_missing_dir(tmp_path):
    assert load_skills(tmp_path / "nope") == []


def test_load_skills_scans_subdirs(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text(VALID, encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "SKILL.md").write_text(
        "---\nname: bskill\ndescription: B\n---\nbody", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("ignore me", encoding="utf-8")
    (tmp_path / "c").mkdir()  # 空目录：跳过

    skills = load_skills(tmp_path)
    assert [s.name for s in skills] == ["weekly-review", "bskill"]


def test_load_skills_skips_duplicate_names(tmp_path):
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "SKILL.md").write_text(VALID, encoding="utf-8")
    assert [s.name for s in load_skills(tmp_path)] == ["weekly-review"]


def test_load_skills_skips_broken_file(tmp_path):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "SKILL.md").write_text(
        "---\nname: ok\ndescription: 好\n---\nbody", encoding="utf-8")
    assert [s.name for s in load_skills(tmp_path)] == ["ok"]


# ---------------------------------------------------------------- Registry

def test_registry_catalog_is_empty_without_skills():
    reg = SkillRegistry([])
    assert reg.catalog_block() == ""
    assert bool(reg) is False
    assert len(reg) == 0


def test_registry_catalog_block_content(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text(VALID, encoding="utf-8")
    reg = SkillRegistry.from_dir(str(tmp_path))

    block = reg.catalog_block()
    assert "【可用技能】" in block
    assert "weekly-review" in block and "每周回顾" in block
    assert "load_skill" in block          # 告诉模型怎么加载
    assert "不授予任何额外权限" in block
    assert reg.body("weekly-review").startswith("# 每周回顾")
    assert reg.body("missing") == ""
    assert reg.get("missing") is None


# ---------------------------------------------------------------- load_skill 工具

def test_skill_tool_returns_body(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text(VALID, encoding="utf-8")
    tool = SkillTool(SkillRegistry.from_dir(str(tmp_path)))

    assert tool.spec.permission.value == "L0"
    assert tool.spec.name == "load_skill"
    assert tool.spec.parameters["required"] == ["name"]

    result = tool.execute({"name": "weekly-review"})
    assert result.ok is True
    assert "【技能：weekly-review】" in result.content
    assert "1. 查时间" in result.content
    assert result.data["skill"] == "weekly-review"


def test_skill_tool_unknown_and_empty(tmp_path):
    tool = SkillTool(SkillRegistry([]))
    assert tool.execute({"name": "nope"}).error_code == "invalid_args"
    assert tool.execute({"name": "  "}).error_code == "invalid_args"


# ---------------------------------------------------------------- 提示词

def test_prompt_unchanged_when_no_skills_block():
    from core.prompt_builder import PromptBuilder

    pb = PromptBuilder(load_persona())
    base = pb.build_system(knowledge_block="K", memory_block="M")
    assert pb.build_system(knowledge_block="K", memory_block="M",
                           skills_block="") == base


def test_prompt_includes_skills_before_speech_style():
    from core.prompt_builder import PromptBuilder

    pb = PromptBuilder(load_persona())
    out = pb.build_system(skills_block="【可用技能】\n- x：y")
    assert "【可用技能】" in out
    assert out.index("【可用技能】") < out.index("【语言风格】")


def test_agent_injects_catalog_only_on_task_path(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text(VALID, encoding="utf-8")
    skills = SkillRegistry.from_dir(str(tmp_path))

    captured: list[str] = []

    class _LLM:
        def stream_chat(self, system, history):
            captured.append(system)
            yield "好"

    agent = Agent(persona=load_persona(), llm=_LLM(), skills=skills)

    "".join(agent.reply("你好"))
    assert "【可用技能】" not in captured[0]  # 快路径不注入

    ctx = agent.begin_task("做个周回顾")
    assert "【可用技能】" in ctx.system_prompt
    assert "weekly-review" in ctx.system_prompt


# ---------------------------------------------------------------- 组合根

def test_runtime_stack_registers_skill_tool(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "SKILL.md").write_text(VALID, encoding="utf-8")
    skills = SkillRegistry.from_dir(str(tmp_path))
    store = SQLiteMemoryStore(":memory:")

    stack = build_runtime_stack(llm=object(), store=store, skills=skills)
    names = [s["function"]["name"] for s in stack.registry.schemas()]
    assert "load_skill" in names

    stack2 = build_runtime_stack(llm=object(), store=store)
    names2 = [s["function"]["name"] for s in stack2.registry.schemas()]
    assert "load_skill" not in names2  # 无技能时不注册（不暴露空工具）
    store.close()
