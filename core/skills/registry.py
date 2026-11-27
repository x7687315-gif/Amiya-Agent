"""SkillRegistry：技能目录（一级）与正文（二级）的读取门面。

只做两件事：
- `catalog_block()`：给 system prompt 的极小目录（名字 + 何时用）；
- `body(name)`：给 `load_skill` 工具的正文。

不做的事：不执行脚本、不解析额外资源、不参与权限判定。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .loader import Skill, load_skills

log = logging.getLogger(__name__)

_CATALOG_HEADER = (
    "【可用技能】\n"
    "下面是可以按需加载的工作指引。需要用到某个技能时，调用 load_skill 工具"
    "取它的完整说明；不要凭名字猜内容，也不要假装已经读过。\n"
    "技能只是做事的方法，不改变你的身份，也不授予任何额外权限。"
)


class SkillRegistry:
    """不可变技能集合（构造后只读）。"""

    def __init__(self, skills: "Optional[List[Skill]]" = None) -> None:
        self._skills: Dict[str, Skill] = {s.name: s for s in (skills or [])}

    @classmethod
    def from_dir(cls, root: "str") -> "SkillRegistry":
        return cls(load_skills(root))

    def __len__(self) -> int:
        return len(self._skills)

    def __bool__(self) -> bool:
        return bool(self._skills)

    @property
    def names(self) -> List[str]:
        return list(self._skills.keys())

    def get(self, name: str) -> "Optional[Skill]":
        return self._skills.get((name or "").strip())

    def body(self, name: str) -> str:
        skill = self.get(name)
        return skill.body if skill is not None else ""

    def catalog_block(self) -> str:
        """一级目录。没有技能时返回空串（调用方据此完全不改提示词）。"""
        if not self._skills:
            return ""
        lines: List[str] = []
        for skill in self._skills.values():
            line = f"- {skill.name}：{skill.description}"
            if skill.when_to_use:
                line += f"（适用于：{skill.when_to_use}）"
            if skill.tools:
                line += f"[用到工具：{'、'.join(skill.tools)}]"
            lines.append(line)
        return _CATALOG_HEADER + "\n\n" + "\n".join(lines)
