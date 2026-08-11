"""PersonaManager：Persona 子系统门面。

聚合 固定身份(Persona) + 行为准则(BehaviorRules) + 动态关系(RelationshipManager)，
为 Agent / UI 提供可直接注入提示词的块：
- behavior_block()     → 【行为准则与边界】
- relationship_block() → 【与用户的关系】（动态，来自 persona_state）
- values_block()       → 【助手的视角与价值观】（供测试/扩展）
"""
from __future__ import annotations

from typing import List, Optional

from .behavior_rules import BehaviorRules
from .loader import load_behavior
from .persona import Persona, load_persona
from .relationship import RelationshipManager


class PersonaManager:
    def __init__(
        self,
        persona: Optional[Persona] = None,
        *,
        memory=None,
        base_dir: str | None = None,
    ) -> None:
        self.persona = persona or load_persona(base_dir)
        self._behavior = BehaviorRules(load_behavior(base_dir).get("guidelines"))
        self._relationship = RelationshipManager(memory=memory, base_dir=base_dir)

    def behavior_block(self) -> Optional[str]:
        """【行为准则与边界】：行为准则 + 知识边界（knowledge_scope）。"""
        parts: List[str] = []
        beh = self._behavior.block()
        if beh:
            parts.append(beh)
        scope = self.persona.identity.get("knowledge_scope")
        if isinstance(scope, dict):
            known = scope.get("熟悉")
            unk = scope.get("不了解")
            lines = []
            if known:
                lines.append(
                    "你熟悉："
                    + ("、".join(known) if isinstance(known, list) else str(known))
                )
            if unk:
                lines.append(
                    "你不了解："
                    + ("、".join(unk) if isinstance(unk, list) else str(unk))
                    + "——遇到这类问题，坦诚表示你不知道即可，不要编造。"
                )
            if lines:
                parts.append("【知识边界】\n" + "\n".join(f"- {s}" for s in lines))
        return "\n\n".join(parts) if parts else None

    def relationship_block(self) -> Optional[str]:
        return self._relationship.block()

    def values_block(self) -> str:
        idn = self.persona.identity
        lines: List[str] = []
        perspective = idn.get("perspective")
        if isinstance(perspective, list):
            lines.append("认知视角：")
            lines += [f"- {x}" for x in perspective]
        values = idn.get("values")
        if isinstance(values, list):
            lines.append("价值观：")
            lines += [f"- {v}" for v in values]
        thinking = idn.get("thinking_style")
        if isinstance(thinking, list):
            lines.append("思维方式：")
            lines += [f"- {t}" for t in thinking]
        return (
            "【助手的视角与价值观】\n" + "\n".join(lines) if lines else ""
        )
