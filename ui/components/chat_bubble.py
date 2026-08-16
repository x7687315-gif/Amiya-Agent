"""聊天气泡组件。"""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from ui.theme import c, t, sp, r, layout
from ui.components.avatar import make_avatar
from ui.components.speaker import MuteState
from ui.design.avatar_provider import AvatarProvider


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
                        bottom_left=layout.BUBBLE_TAIL,
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
        avatar_provider: AvatarProvider | None = None,
        max_width_ratio: float = layout.AI_BUBBLE_MAX_RATIO,
        on_speak: Callable[[str], None] | None = None,
        mute_state: Optional["MuteState"] = None,
    ) -> ft.Control:
        """助手气泡（左侧，白底描边）。

        Args:
            text: 初始文本；若提供 text_control 则忽略。
            text_control: 可选的 ft.Text 控件，用于流式追加。
            on_speak: 可选朗读回调；提供则在气泡末尾追加 🔊 按钮，
                点击时朗读「本气泡自己的文本」（读 text_control.value，而非全局最新回复）。
            mute_state: 可选全局静音状态；提供则 🔊 按钮订阅它，静音时自动隐藏。
        """
        content = text_control or ft.Text(
            text,
            color=c.TEXT_PRIMARY,
            size=t.BODY,
            selectable=True,
        )
        row: list[ft.Control] = [
            make_avatar(avatar_provider, state_key="calm", radius=16, text_size=13),
            ft.Container(
                content=content,
                bgcolor=c.SURFACE,
                border=ft.Border.all(width=1, color=c.BORDER),
                padding=ft.Padding.only(
                    left=sp.MD + 2, right=sp.MD + 2, top=sp.SM + 1, bottom=sp.SM + 1
                ),
                border_radius=ft.BorderRadius.only(
                    top_left=layout.BUBBLE_TAIL,
                    top_right=r.LG,
                    bottom_left=r.LG,
                    bottom_right=r.LG,
                ),
                margin=ft.Margin.only(left=sp.SM, right=64, top=4, bottom=4),
                expand=bool(on_speak),  # 有朗读键时撑满，把 🔊 推到气泡右侧
            ),
        ]

        # M3-B：每个最终助手气泡末尾挂一个 🔊，点它只读这一句
        if on_speak is not None:
            speaker = ft.IconButton(
                icon=ft.Icons.VOLUME_UP,
                icon_color=c.PRIMARY,
                icon_size=16,
                tooltip="朗读这句",
                on_click=lambda _e: on_speak(content.value or ""),
            )

            def _apply_mute(muted: bool) -> None:
                # 订阅静音状态：静音时隐藏 🔊；无 page 时跳过 update 避免 RuntimeError
                speaker.visible = not muted
                try:
                    speaker.update()
                except Exception:  # noqa: BLE001 - 未挂载控件 update 会抛，忽略即可
                    pass

            if mute_state is not None:
                mute_state.subscribe(_apply_mute)
            else:
                speaker.visible = True
            row.append(speaker)

        return ft.Row(
            row,
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
