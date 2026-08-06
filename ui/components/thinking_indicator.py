"""思考中指示器。"""
from __future__ import annotations

import flet as ft

from ui.theme import c, t, sp, layout
from ui.components.avatar import make_avatar
from ui.design.avatar_provider import AvatarProvider


class ThinkingIndicator(ft.Row):
    """助手正在思考中的视觉反馈（轻量三点提示，保留以兼容旧调用）。"""

    def __init__(self, avatar_provider: AvatarProvider | None = None) -> None:
        super().__init__()
        self.spacing = sp.SM
        self.vertical_alignment = ft.CrossAxisAlignment.CENTER
        self.controls = [
            make_avatar(avatar_provider, state_key="thinking", radius=16, text_size=13),
            ft.Text(
                "助手正在思考…",
                color=c.TEXT_MUTED,
                size=t.CAPTION,
                italic=True,
            ),
        ]
