"""输入栏组件：多行输入框 + 发送按钮 + 字数提示。"""
from __future__ import annotations

from typing import Callable

import flet as ft

from ui.theme import ALIGN_CENTER_RIGHT, c, t, sp, r


class InputBar(ft.Container):
    """底部输入栏。"""

    def __init__(
        self,
        on_send: Callable[[str], None],
        max_lines: int = 4,
    ) -> None:
        super().__init__()
        self.on_send = on_send
        self._max_lines = max_lines
        self._is_loading = False

        self._send_btn = ft.IconButton(
            icon=ft.Icons.SEND_ROUNDED,
            icon_color="#fff",
            bgcolor=c.PRIMARY,
            tooltip="发送",
            width=44,
            height=44,
            disabled=True,
            on_click=self._handle_send,
        )

        self._hint_counter = ft.Text(
            "0/2000",
            color=c.TEXT_MUTED,
            size=t.TINY,
            visible=False,
        )

        self._field = ft.TextField(
            hint_text="想和助手聊点什么？",
            hint_style=ft.TextStyle(color=c.TEXT_MUTED),
            text_style=ft.TextStyle(color=c.TEXT_PRIMARY, size=t.BODY),
            bgcolor=c.SURFACE_SECONDARY,
            border=ft.InputBorder.NONE,
            border_radius=ft.BorderRadius.only(
                top_left=r.MD, top_right=r.MD, bottom_left=r.MD, bottom_right=r.MD
            ),
            content_padding=ft.Padding.only(left=sp.LG, right=sp.LG, top=12, bottom=12),
            cursor_color=c.PRIMARY,
            multiline=True,
            min_lines=1,
            max_lines=max_lines,
            expand=True,
            on_submit=self._handle_send,
            on_change=self._on_change,
            on_focus=self._on_focus,
            on_blur=self._on_blur,
        )

        self._build()

    def _build(self) -> None:
        self.bgcolor = c.SURFACE
        self.padding = ft.Padding.only(left=sp.LG, right=sp.LG, top=sp.SM, bottom=12)
        self.border = ft.Border.only(top=ft.BorderSide(width=1, color=c.BORDER))
        self.content = ft.Column(
            [
                ft.Row(
                    [self._field, self._send_btn],
                    spacing=sp.SM,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                ft.Container(
                    content=self._hint_counter,
                    alignment=ALIGN_CENTER_RIGHT,
                    height=14,
                ),
            ],
            spacing=2,
        )

    def _on_change(self, e: ft.ControlEvent) -> None:
        text = (self._field.value or "").strip()
        length = len(self._field.value or "")
        self._hint_counter.value = f"{length}/2000"
        self._update_send_btn(bool(text))

    def _on_focus(self, _e: ft.ControlEvent) -> None:
        self._hint_counter.visible = True
        self._hint_counter.update()

    def _on_blur(self, _e: ft.ControlEvent) -> None:
        if not (self._field.value or "").strip():
            self._hint_counter.visible = False
            self._hint_counter.update()

    def _update_send_btn(self, enabled: bool) -> None:
        if self._is_loading:
            return
        self._send_btn.disabled = not enabled
        self._send_btn.bgcolor = c.PRIMARY if enabled else c.SURFACE_SECONDARY
        self._send_btn.icon_color = "#fff" if enabled else c.TEXT_MUTED
        self._send_btn.update()

    def _handle_send(self, _e: ft.ControlEvent | None) -> None:
        text = (self._field.value or "").strip()
        if not text or self._is_loading:
            return
        self._field.value = ""
        self._hint_counter.value = "0/2000"
        self._hint_counter.visible = False
        self._field.update()
        self._hint_counter.update()
        self._update_send_btn(False)
        self.on_send(text)

    def set_loading(self, loading: bool) -> None:
        """设置发送加载状态。"""
        self._is_loading = loading
        self._field.disabled = loading
        if loading:
            self._send_btn.icon = ft.ProgressRing(color=c.PRIMARY, width=18, height=18, stroke_width=2)
            self._send_btn.disabled = True
            self._send_btn.bgcolor = c.SURFACE_SECONDARY
        else:
            self._send_btn.icon = ft.icons.SEND_ROUNDED
            self._update_send_btn(bool((self._field.value or "").strip()))
        self._send_btn.update()
        self._field.update()

    def focus(self) -> None:
        self._field.focus()
