"""SkillTool：让模型按需加载技能正文（三级加载的第二级）。

为什么是一个本地工具而不是把正文塞进 system prompt：
- 正文可能几千字，常驻会挤压上下文并抬高每轮成本；
- 目录级（name/description）足够让模型判断"要不要用"；
- 加载动作本身是**可观察**的（RunCard 会显示「加载技能：xxx」），用户看得到它读了什么。

安全边界：
- L0 只读本地文本，无副作用；
- 名字必须在注册表里（不是文件系统路径），杜绝 `../` 之类的路径穿越；
- 技能正文是**指引**，不授予权限——后续工具调用仍逐个过 Policy。
"""
from __future__ import annotations

from ..tools.base import PermissionLevel, ToolResult, ToolSpec
from .registry import SkillRegistry


class SkillTool:
    """按名字加载技能正文。"""

    def __init__(self, registry: SkillRegistry) -> None:
        self._registry = registry
        self.spec = ToolSpec(
            name="load_skill",
            description=(
                "加载某个技能的完整工作指引。目录里只给了技能名和用途，"
                "真正要用时必须先调用本工具取正文；不要凭名字猜测内容。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "技能名（见【可用技能】目录）",
                        "maxLength": 64,
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            permission=PermissionLevel.L0,
            timeout_s=5.0,
            # 正文本身在 loader 已截到 8000 字符，这里留一点余量给标题
            result_limit=9000,
        )

    def execute(self, args: dict, *, cancel=None) -> ToolResult:
        name = str(args.get("name") or "").strip()
        if not name:
            return ToolResult.fail("invalid_args", "load_skill 需要 name")
        skill = self._registry.get(name)
        if skill is None:
            available = "、".join(self._registry.names) or "（无）"
            return ToolResult.fail(
                "invalid_args", f"未知技能：{name}。可用技能：{available}")
        if not skill.body:
            return ToolResult.fail("execution_error", f"技能 {name} 没有正文内容")
        return ToolResult.success(
            f"【技能：{skill.name}】{skill.description}\n\n{skill.body}",
            data={"skill": skill.name, "chars": len(skill.body)},
        )
