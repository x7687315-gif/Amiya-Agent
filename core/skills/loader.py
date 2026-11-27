"""Skill 加载：把 `skills/<name>/SKILL.md` 解析成可注入的「工作指引」。

三级渐进加载（docs/2x/ARCHITECTURE_2.0.md §6）：
1. **目录级**（常驻）：name + description + when_to_use，量很小，进 system prompt；
2. **正文级**（按需）：只有模型调用 `load_skill` 时，才把 body 作为工具结果返回；
3. **资源级**（未实现）：SKILL.md 里引用的附加文件/脚本，本阶段不读、不执行。

边界（红线）：
- 技能是**文字指引**，不是权限凭证——它声明的工具仍要逐个过 PermissionPolicy。
- 本模块**从不执行**任何脚本；只读文本文件。
- 解析失败只跳过并记日志，绝不让一个坏文件拖垮启动。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

log = logging.getLogger(__name__)

# 与本地工具名同风格：小写字母开头，允许数字/下划线/连字符
_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
MAX_BODY_CHARS = 8000  # 正文上限：防止把整个文档塞进工具结果
_SKILL_FILES = ("SKILL.md", "skill.md")


@dataclass(frozen=True)
class Skill:
    """一个技能（只读、不可变）。"""

    name: str
    description: str
    when_to_use: str = ""
    tools: tuple = ()          # 声明会用到的本地工具名（仅提示，不授予权限）
    body: str = ""             # 二级内容：按需加载
    path: str = ""


def parse_skill_md(text: str, *, fallback_name: str = "", path: str = "") -> Optional[Skill]:
    """解析 SKILL.md（frontmatter + 正文）。无法解析返回 None，不抛异常。"""
    if not text or not text.strip():
        return None

    front: dict = {}
    body = text
    stripped = text.lstrip("\ufeff")
    if stripped.startswith("---"):
        # 只认文件开头的 frontmatter；正文里的 --- 不当分隔符
        end = stripped.find("\n---", 3)
        if end == -1:
            log.warning("SKILL.md frontmatter 未闭合（%s）", path or fallback_name)
            return None
        raw = stripped[3:end]
        body = stripped[end + 4:]
        try:
            loaded = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            log.warning("SKILL.md frontmatter 解析失败（%s）：%s", path or fallback_name, exc)
            return None
        if not isinstance(loaded, dict):
            log.warning("SKILL.md frontmatter 不是映射（%s）", path or fallback_name)
            return None
        front = loaded

    name = str(front.get("name") or fallback_name or "").strip()
    if not _NAME_RE.match(name):
        log.warning("技能名非法（%s）：%r", path or fallback_name, name)
        return None
    description = str(front.get("description") or "").strip()
    if not description:
        log.warning("技能缺少 description（%s）", name)
        return None

    tools_raw = front.get("tools") or []
    if isinstance(tools_raw, str):
        tools_raw = [tools_raw]
    tools = tuple(str(t).strip() for t in tools_raw if str(t).strip()) if isinstance(
        tools_raw, (list, tuple)) else ()

    body = (body or "").strip()
    if len(body) > MAX_BODY_CHARS:
        log.warning("技能正文超长，已截断（%s，%d 字符）", name, len(body))
        body = body[:MAX_BODY_CHARS] + "\n…（正文已截断）"

    return Skill(
        name=name,
        description=description,
        when_to_use=str(front.get("when_to_use") or "").strip(),
        tools=tools,
        body=body,
        path=path,
    )


def load_skills(root: "str | Path", *, limit: int = 50) -> List[Skill]:
    """扫描目录下的一级子目录，读取各自的 SKILL.md。目录不存在返回空列表。"""
    base = Path(root)
    if not base.exists() or not base.is_dir():
        return []

    out: List[Skill] = []
    seen: set = set()
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        for filename in _SKILL_FILES:
            candidate = child / filename
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("读取技能文件失败（%s）：%s", candidate, exc)
                break
            skill = parse_skill_md(text, fallback_name=child.name, path=str(candidate))
            if skill is None:
                break
            if skill.name in seen:
                log.warning("技能名重复，已跳过：%s（%s）", skill.name, candidate)
                break
            seen.add(skill.name)
            out.append(skill)
            if len(out) >= limit:
                return out
            break
    return out


def skill_to_dict(skill: Skill) -> dict:
    """供测试/调试的输出形态（不用于模型）。"""
    return {
        "name": skill.name, "description": skill.description,
        "when_to_use": skill.when_to_use, "tools": list(skill.tools),
        "body_chars": len(skill.body), "path": skill.path,
    }
