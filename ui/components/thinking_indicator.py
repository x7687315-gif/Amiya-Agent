"""思考中指示器。"""
from __future__ import annotations

import flet as ft

from ui.theme import c, t, sp, r, layout


class ThinkingIndicator(ft.Row):
    """助手正在思考中的视觉反馈。"""

    def __init__(self) -> None:
        super().__init__()
        self.spacing = sp.SM
        self.vertical_alignment = ft.CrossAxisAlignment.CENTER
        self.controls = [
            ft.CircleAvatar(
                bgcolor=c.PRIMARY,
                radius=16,
                content=ft.Text(
                    layout.AI_AVATAR_TEXT,
                    color="#fff",
                    size=13,
                    weight=ft.FontWeight.BOLD,
                ),
            ),
            ft.Text(
                "助手正在思考…",
                color=c.TEXT_MUTED,
                size=t.CAPTION,
                italic=True,
            ),
        ]
