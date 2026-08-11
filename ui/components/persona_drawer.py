"""人格抽屉组件：从右侧滑出，展示助手的设定。

Phase 3 起：抽屉从 persona 子系统读取配置——
- 视角与认知 / 价值观 / 思考方式：identity.yaml（persona.identity）
- 行为准则：behavior.yaml
- 与用户的关系：relationship.yaml（默认阶段背景）
"""
from __future__ import annotations

from typing import Any, Dict, List

import flet as ft

from core.persona.loader import load_behavior, load_relationship
from ui.theme import ALIGN_CENTER, c, t, sp, r
from ui.components.avatar import make_avatar
from ui.design.avatar_provider import AvatarProvider


class PersonaDrawer(ft.NavigationDrawer):
    """右侧人格信息抽屉。"""

    def __init__(self, persona, avatar_provider: AvatarProvider | None = None) -> None:
        super().__init__()
        self.persona = persona
        self._avatar_provider = avatar_provider
        self.bgcolor = c.SURFACE
        self.controls = self._build_content()

    def _build_content(self) -> List[ft.Control]:
        identity = self.persona.identity
        tags = self._extract_tags(identity)
        sections = [
            ("视角与认知", "perspective"),
            ("价值观", "values"),
            ("思考方式", "thinking_style"),
        ]

        controls: List[ft.Control] = [
            ft.Container(
                content=ft.Row(
                    [
                        ft.Text("关于助手", size=t.HEADLINE, weight=ft.FontWeight.BOLD, color=c.TEXT_PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=ft.Padding.only(left=sp.XL, right=sp.MD, top=sp.XL, bottom=sp.LG),
            ),
            ft.Container(
                content=ft.Column(
                    [
                        make_avatar(
                            self._avatar_provider,
                            state_key="calm",
                            radius=36,
                            text_size=24,
                            bgcolor=c.PRIMARY_LIGHT,
                            text_color=c.PRIMARY,
                        ),
                        ft.Text(
                            self.persona.name,
                            size=20,
                            weight=ft.FontWeight.BOLD,
                            color=c.TEXT_PRIMARY,
                        ),
                        ft.Text(
                            self.persona.title or "",
                            size=t.CAPTION,
                            color=c.TEXT_SECONDARY,
                        ),
                    ],
                    spacing=sp.SM,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.only(left=sp.XL, right=sp.XL, bottom=sp.LG),
                alignment=ALIGN_CENTER,
            ),
        ]

        if tags:
            controls.append(
                ft.Container(
                    content=ft.Row(
                        [self._tag(label) for label in tags],
                        spacing=sp.SM,
                        alignment=ft.MainAxisAlignment.CENTER,
                        wrap=True,
                    ),
                    padding=ft.Padding.only(left=sp.XL, right=sp.XL, bottom=sp.LG),
                )
            )

        for title, key in sections:
            section_text = self._format_list(identity.get(key))
            if section_text:
                controls.append(self._section(title, section_text))

        # 行为准则（来自 behavior.yaml）
        behavior = load_behavior().get("guidelines", [])
        if behavior:
            controls.append(self._section("行为准则", self._format_list(behavior)))

        # 与用户的关系（来自 relationship.yaml 默认阶段背景）
        rel = load_relationship()
        rel_stage = rel.get("default_stage", "信任")
        rel_bg = rel.get("stages", {}).get(rel_stage, {}).get("background", [])
        if rel_bg:
            controls.append(
                self._section(f"与用户的关系（{rel_stage}）", self._format_list(rel_bg))
            )

        controls.append(ft.Container(height=sp.XL))
        return controls

    def _section(self, title: str, text: str) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=t.TINY, weight=ft.FontWeight.BOLD, color=c.PRIMARY),
                    ft.Text(text, size=t.BODY, color=c.TEXT_PRIMARY),
                ],
                spacing=sp.SM,
            ),
            padding=ft.Padding.only(left=sp.XL, right=sp.XL, bottom=sp.LG),
        )

    def _tag(self, label: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(label, size=t.TINY, color=c.TEXT_SECONDARY),
            bgcolor=c.SURFACE_SECONDARY,
            border_radius=r.SM,
            padding=ft.Padding.only(left=sp.SM + 4, right=sp.SM + 4, top=sp.XS, bottom=sp.XS),
        )

    def _extract_tags(self, identity: Dict[str, Any]) -> List[str]:
        """从 behavior.yaml 行为准则 + identity 价值观中提取行为标签。"""
        tags: List[str] = []
        guidelines = load_behavior().get("guidelines", [])
        values = identity.get("values", [])
        keywords = {
            "温柔": "温柔",
            "认真": "认真",
            "责任": "责任感",
            "担当": "有担当",
            "不放弃": "坚韧",
            "诚实": "诚实",
            "透明": "透明",
            "沟通": "善于沟通",
        }
        for line in list(guidelines) + list(values):
            if not isinstance(line, str):
                continue
            for kw, tag in keywords.items():
                if kw in line and tag not in tags:
                    tags.append(tag)
        return tags[:6]

    def _format_list(self, value: Any) -> str:
        if isinstance(value, list):
            return "\n".join(f"· {item}" for item in value if isinstance(item, str))
        if isinstance(value, str):
            return value
        return ""
