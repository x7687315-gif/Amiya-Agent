"""顶部栏组件：头像、名字、称号、状态、人格抽屉入口。"""
from __future__ import annotations

from typing import Callable

import flet as ft

from ui.theme import c, t, sp, r, layout


class Header(ft.Container):
    """顶部栏，展示助手身份与当前状态。"""

    def __init__(self, persona, on_persona_click: Callable[[], None] | None = None) -> None:
        super().__init__()
        self.persona = persona
        self.on_persona_click = on_persona_click
        self._status_text = ft.Text("在线", color=c.TEXT_MUTED, size=12)
        self._status_dot = ft.Container(
            width=8,
            height=8,
            bgcolor=c.SUCCESS,
            border_radius=r.FULL,
        )
        self._persona_btn = ft.IconButton(
            icon=ft.Icons.BADGE_OUTLINED,
            icon_color=c.TEXT_SECONDARY,
            tooltip="查看人格",
            on_click=lambda _e: self.on_persona_click() if self.on_persona_click else None,
        )
        self._build()

    def _build(self) -> None:
        self.bgcolor = c.SURFACE
        self.padding = ft.Padding.only(left=sp.LG, right=sp.LG, top=10, bottom=10)
        self.border = ft.Border.only(bottom=ft.BorderSide(width=1, color=c.BORDER))
        self.height = layout.HEADER_HEIGHT
        self.content = ft.Row(
            [
                ft.CircleAvatar(
                    bgcolor=c.PRIMARY,
                    radius=20,
                    content=ft.Text(
                        layout.AI_AVATAR_TEXT,
                        color="#fff",
                        size=16,
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
                ft.Column(
                    [
                        ft.Text(
                            self.persona.name,
                            color=c.TEXT_PRIMARY,
                            size=t.TITLE,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(
                            self.persona.title or "本地",
                            color=c.TEXT_SECONDARY,
                            size=t.CAPTION,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                ft.Row(
                    [
                        self._status_dot,
                        self._status_text,
                    ],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                self._persona_btn,
            ],
            spacing=sp.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def set_thinking(self, thinking: bool) -> None:
        """切换为思考中状态。"""
        if thinking:
            self._status_text.value = "正在思考…"
            self._status_text.color = c.PRIMARY
            self._status_dot = ft.ProgressRing(
                width=10,
                height=10,
                color=c.PRIMARY,
                stroke_width=2,
            )
        else:
            self._status_text.value = "在线"
            self._status_text.color = c.TEXT_MUTED
            self._status_dot = ft.Container(
                width=8,
                height=8,
                bgcolor=c.SUCCESS,
                border_radius=r.FULL,
            )
        # 重建状态区：简单替换 Row 中的子项
        row = self.content
        assert isinstance(row, ft.Row)
        row.controls[-2] = ft.Row(
            [self._status_dot, self._status_text],
            spacing=6,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.update()


