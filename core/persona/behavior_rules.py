"""行为准则：从 behavior.yaml 显式读取行为规则，提供 block() 拼装。

行为准则描述「助手应该怎么做」，与角色知识（客观设定）严格区分。
"""
from __future__ import annotations

from typing import List, Optional

from .loader import load_behavior


class BehaviorRules:
    def __init__(self, guidelines: Optional[List[str]] = None) -> None:
        self.guidelines = (
            guidelines
            if guidelines is not None
            else load_behavior().get("guidelines", [])
        )

    def block(self) -> str:
        """渲染【行为准则】定界块（无规则时返回空串）。"""
        if not self.guidelines:
            return ""
        return "【行为准则】\n" + "\n".join(f"- {g}" for g in self.guidelines)
