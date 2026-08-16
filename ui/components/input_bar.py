"""输入栏组件：多行输入框 + 发送/停止按钮 + 字数提示。

生成中（loading）且提供 on_stop 时，发送键变成红色停止键——满足总设计
「停止生成」：流式卡住不必干等 30s 超时。未提供 on_stop 时保持旧行为
（转圈禁用），兼容现有调用方与测试。
"""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from ui.theme import ALIGN_CENTER_RIGHT, c, t, sp, r, layout, anim


class InputBar(ft.Container):
    """底部输入栏。"""

    def __init__(
        self,
        on_send: Callable[[str], None],
        max_lines: int = 4,
        on_stop: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self.on_send = on_send
        self.on_stop = on_stop
        self._max_lines = max_lines
        self._is_loading = False

        self._send_btn = ft.IconButton(
            icon=ft.Icons.SEND_ROUNDED,
            icon_color=c.ON_PRIMARY,
            bgcolor=c.PRIMARY,
            tooltip="发送",
            width=44,
            height=44,
            disabled=True,
            on_click=self._handle_send,
        )

        self._hint_counter = ft.Text(
            f"0/{layout.INPUT_MAX_LENGTH}",
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

        # 入场淡入（由 app 在 page.add 后调用 reveal 触发）
        self.opacity = 0
        self.animate_opacity = ft.Animation(anim.NORMAL, anim.EASE_OUT)

    def _build(self) -> None:
        # 透明化：壁纸为全局统一底色，输入框本体仍是浅色圆角卡片
        self.bgcolor = None
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
        self._hint_counter.value = f"{length}/{layout.INPUT_MAX_LENGTH}"
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
        self._send_btn.icon_color = c.ON_PRIMARY if enabled else c.TEXT_MUTED
        self._send_btn.update()

    def _handle_send(self, _e: ft.ControlEvent | None) -> None:
        text = (self._field.value or "").strip()
        if not text or self._is_loading:
            return
        self._field.value = ""
        self._hint_counter.value = f"0/{layout.INPUT_MAX_LENGTH}"
        self._hint_counter.visible = False
        self._field.update()
        self._hint_counter.update()
        self._update_send_btn(False)
        self.on_send(text)

    def _handle_stop(self, _e: ft.ControlEvent | None) -> None:
        if self._is_loading and self.on_stop is not None:
            self.on_stop()

    def set_loading(self, loading: bool) -> None:
        """设置发送加载状态。

        loading=True 且注入了 on_stop：发送键切换为「停止生成」键（可点击）；
        否则维持原转圈禁用行为。False 时一律恢复发送键。
        """
        self._is_loading = loading
        self._field.disabled = loading
        if loading:
            if self.on_stop is not None:
                self._send_btn.icon = ft.Icons.STOP_CIRCLE_ROUNDED
                self._send_btn.tooltip = "停止生成"
                self._send_btn.disabled = False
                self._send_btn.bgcolor = c.ERROR
                self._send_btn.icon_color = c.ON_PRIMARY
                self._send_btn.on_click = self._handle_stop
            else:
                self._send_btn.icon = ft.ProgressRing(color=c.PRIMARY, width=18, height=18, stroke_width=2)
                self._send_btn.disabled = True
                self._send_btn.bgcolor = c.SURFACE_SECONDARY
        else:
            self._send_btn.icon = ft.Icons.SEND_ROUNDED
            self._send_btn.tooltip = "发送"
            self._send_btn.on_click = self._handle_send
            self._send_btn.icon_color = c.TEXT_MUTED
            self._update_send_btn(bool((self._field.value or "").strip()))
        self._send_btn.update()
        self._field.update()

    def reveal(self) -> None:
        """入场淡入（由 app 在装载后调用）。"""
        self.opacity = 1
        self.update()

    def focus(self) -> None:
        self._field.focus()
