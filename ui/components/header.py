"""顶部栏组件：头像、名字、称号、状态、人格抽屉入口。"""
from __future__ import annotations

from typing import Callable

import flet as ft

from ui.theme import c, t, sp, r, layout, anim
from ui.components.avatar import make_avatar
from ui.design.avatar_provider import AvatarProvider


class Header(ft.Container):
    """顶部栏，展示助手身份与当前状态。"""

    def __init__(
        self,
        persona,
        on_persona_click: Callable[[], None] | None = None,
        avatar_provider: AvatarProvider | None = None,
    ) -> None:
        super().__init__()
        self.persona = persona
        self.on_persona_click = on_persona_click
        self._avatar_provider = avatar_provider

        self._status_text = ft.Text("在线", color=c.TEXT_MUTED, size=t.CAPTION)
        self._status_dot = self._build_dot()
        self._status_row = ft.Row(
            [self._status_dot, self._status_text],
            spacing=sp.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._persona_btn = ft.IconButton(
            icon=ft.Icons.BADGE_OUTLINED,
            icon_color=c.TEXT_SECONDARY,
            tooltip="查看人格",
            on_click=lambda _e: self.on_persona_click() if self.on_persona_click else None,
        )
        self._build()

        # 入场淡入（由 app 在 page.add 后调用 reveal 触发）
        self.opacity = 0
        self.animate_opacity = ft.Animation(anim.NORMAL, anim.EASE_OUT)

    def _build_dot(self) -> ft.Container:
        return ft.Container(width=8, height=8, bgcolor=c.SUCCESS, border_radius=r.FULL)

    def _build(self) -> None:
        self.bgcolor = c.SURFACE
        self.padding = ft.Padding.only(
            left=sp.LG, right=sp.LG, top=layout.HEADER_PAD_Y, bottom=layout.HEADER_PAD_Y
        )
        self.border = ft.Border.only(bottom=ft.BorderSide(width=1, color=c.BORDER))
        self.height = layout.HEADER_HEIGHT
        self.content = ft.Row(
            [
                make_avatar(self._avatar_provider, state_key="calm", radius=20, text_size=16),
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
                self._status_row,
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
            self._status_dot = ft.ProgressRing(width=10, height=10, color=c.PRIMARY, stroke_width=2)
        else:
            self._status_text.value = "在线"
            self._status_text.color = c.TEXT_MUTED
            self._status_dot = self._build_dot()
        # 直接替换已保存的状态行引用，避免依赖 Row 子项索引
        self._status_row.controls[0] = self._status_dot
        self._status_row.update()

    def reveal(self) -> None:
        """入场淡入（由 app 在装载后调用）。"""
        self.opacity = 1
        self.update()
