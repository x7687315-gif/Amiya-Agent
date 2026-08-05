"""人格抽屉组件：从右侧滑出，展示助手的设定。"""
from __future__ import annotations

from typing import Any, Dict, List

import flet as ft

from ui.theme import ALIGN_CENTER, c, t, sp, r


class PersonaDrawer(ft.NavigationDrawer):
    """右侧人格信息抽屉。"""

    def __init__(self, persona) -> None:
        super().__init__()
        self.persona = persona
        self.bgcolor = c.SURFACE
        self.controls = self._build_content()

    def _build_content(self) -> List[ft.Control]:
        identity = self.persona.identity
        tags = self._extract_tags(identity)
        sections = [
            ("世界观", "worldview"),
            ("价值观", "values"),
            ("思考方式", "thinking_style"),
            ("与用户的关系", "relationship_to_doctor"),
            ("行为准则", "behavior_guidelines"),
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
                        ft.CircleAvatar(
                            bgcolor=c.PRIMARY_LIGHT,
                            radius=36,
                            content=ft.Text(
                                "阿",
                                color=c.PRIMARY,
                                size=24,
                                weight=ft.FontWeight.BOLD,
                            ),
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
                controls.append(
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Text(title, size=t.TINY, weight=ft.FontWeight.BOLD, color=c.PRIMARY),
                                ft.Text(section_text, size=t.BODY, color=c.TEXT_PRIMARY),
                            ],
                            spacing=sp.SM,
                        ),
                        padding=ft.Padding.only(left=sp.XL, right=sp.XL, bottom=sp.LG),
                    )
                )

        controls.append(ft.Container(height=sp.XL))
        return controls

    def _tag(self, label: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(label, size=t.TINY, color=c.TEXT_SECONDARY),
            bgcolor=c.SURFACE_SECONDARY,
            border_radius=r.SM,
            padding=ft.Padding.only(left=sp.SM + 4, right=sp.SM + 4, top=sp.XS, bottom=sp.XS),
        )

    def _extract_tags(self, identity: Dict[str, Any]) -> List[str]:
        """从 behavior_guidelines / values 中提取行为标签。"""
        tags: List[str] = []
        guidelines = identity.get("behavior_guidelines", [])
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
