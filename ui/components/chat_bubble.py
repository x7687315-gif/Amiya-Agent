"""聊天气泡组件。"""
from __future__ import annotations

import flet as ft

from ui.theme import c, t, sp, r, layout


class ChatBubble:
    """气泡工厂，返回 Flet 控件。"""

    @staticmethod
    def user(text: str, max_width_ratio: float = layout.USER_BUBBLE_MAX_RATIO) -> ft.Control:
        """用户气泡（右侧，淡紫底）。"""
        return ft.Row(
            [
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Text(
                        text,
                        color=c.TEXT_PRIMARY,
                        size=t.BODY,
                        selectable=True,
                    ),
                    bgcolor=c.PRIMARY_LIGHT,
                    padding=ft.Padding.only(
                        left=sp.MD + 2, right=sp.MD + 2, top=sp.SM + 1, bottom=sp.SM + 1
                    ),
                    border_radius=ft.BorderRadius.only(
                        top_left=r.LG,
                        top_right=r.LG,
                        bottom_left=4,
                        bottom_right=r.LG,
                    ),
                    margin=ft.Margin.only(left=64, right=sp.SM, top=4, bottom=4),
                ),
            ],
            spacing=0,
        )

    @staticmethod
    def assistant(
        text: str,
        text_control: ft.Text | None = None,
        max_width_ratio: float = layout.AI_BUBBLE_MAX_RATIO,
    ) -> ft.Control:
        """助手气泡（左侧，白底描边）。

        Args:
            text: 初始文本；若提供 text_control 则忽略。
            text_control: 可选的 ft.Text 控件，用于流式追加。
        """
        content = text_control or ft.Text(
            text,
            color=c.TEXT_PRIMARY,
            size=t.BODY,
            selectable=True,
        )
        return ft.Row(
            [
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
                ft.Container(
                    content=content,
                    bgcolor=c.SURFACE,
                    border=ft.Border.all(width=1, color=c.BORDER),
                    padding=ft.Padding.only(
                        left=sp.MD + 2, right=sp.MD + 2, top=sp.SM + 1, bottom=sp.SM + 1
                    ),
                    border_radius=ft.BorderRadius.only(
                        top_left=4,
                        top_right=r.LG,
                        bottom_left=r.LG,
                        bottom_right=r.LG,
                    ),
                    margin=ft.Margin.only(left=sp.SM, right=64, top=4, bottom=4),
                ),
            ],
            spacing=sp.SM,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    @staticmethod
    def error(text: str) -> ft.Control:
        """系统错误提示气泡（不伪装成助手台词）。"""
        return ft.Row(
            [
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=c.ERROR, size=16),
                            ft.Text(text, color=c.ERROR, size=t.CAPTION),
                        ],
                        spacing=sp.SM,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=c.ERROR_SURFACE,
                    border=ft.Border.all(width=1, color=c.ERROR),
                    border_radius=r.MD,
                    padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.SM, bottom=sp.SM),
                    margin=ft.Margin.only(left=64, right=64, top=sp.SM, bottom=sp.SM),
                ),
                ft.Container(expand=True),
            ],
            spacing=0,
        )
