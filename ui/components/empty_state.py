"""空态 / 欢迎区组件。"""
from __future__ import annotations

import flet as ft

from ui.theme import ALIGN_CENTER, c, t, sp, r, layout


class EmptyState(ft.Container):
    """首次打开或无消息时显示的欢迎区。"""

    def __init__(self, persona) -> None:
        super().__init__()
        self.persona = persona
        self.alignment = ALIGN_CENTER
        self.expand = True
        self.content = ft.Column(
            [
                ft.CircleAvatar(
                    bgcolor=c.PRIMARY_LIGHT,
                    radius=48,
                    content=ft.Text(
                        layout.AI_AVATAR_TEXT,
                        color=c.PRIMARY,
                        size=28,
                        weight=ft.FontWeight.BOLD,
                    ),
                ),
                ft.Text(
                    f"{self.persona.address or '用户'}，欢迎回来。",
                    color=c.TEXT_PRIMARY,
                    size=t.DISPLAY,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Text(
                    "今天想和助手聊些什么？",
                    color=c.TEXT_SECONDARY,
                    size=t.BODY,
                ),
            ],
            spacing=sp.LG,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
