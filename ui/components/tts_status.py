"""TTS 状态外显（审计补强）：忙碌状态（observable）+ 服务不可用提示条。

- TTSStatusState：合成/播放忙碌的全局可观察状态。订阅者（每个气泡的 🔊）
  在忙碌时禁用并换转圈图标——顺带把「连点多个气泡并发合成」收敛为串行。
- TTSStatusBanner：TTS 不可用提示条（可手动关闭）。落实总设计 §16 的
  「TTS 挂了聊天不能跟着崩，但要告知『语音服务暂不可用』」——此前只有日志，
  用户无感知。

两者均为纯 UI 组件，不 import core.tts（依赖方向：UI 状态层 ← app 接线）。
"""
from __future__ import annotations

from typing import Callable, Set

import flet as ft

from ui.theme import c, t, sp, r


class TTSStatusState:
    """语音链路忙碌状态（合成中 + 排队播放中都算忙）。"""

    def __init__(self) -> None:
        self._busy = False
        self._subs: Set[Callable[[bool], None]] = set()

    @property
    def busy(self) -> bool:
        return self._busy

    def subscribe(self, cb: Callable[[bool], None]) -> None:
        """订阅忙碌变化；注册时立即回调一次对齐当前状态。"""
        self._subs.add(cb)
        try:
            cb(self._busy)
        except Exception:  # noqa: BLE001
            pass

    def unsubscribe(self, cb: Callable[[bool], None]) -> None:
        self._subs.discard(cb)

    def set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            return
        self._busy = busy
        for cb in list(self._subs):
            try:
                cb(self._busy)
            except Exception:  # noqa: BLE001
                pass


class TTSStatusBanner(ft.Container):
    """「语音服务暂不可用」提示条。默认隐藏，show 后可点 × 关闭。"""

    def __init__(self) -> None:
        super().__init__()
        self._text = ft.Text("", color=c.TEXT_SECONDARY, size=t.CAPTION, expand=True)
        self._close_btn = ft.IconButton(
            icon=ft.Icons.CLOSE_ROUNDED,
            icon_size=14,
            icon_color=c.TEXT_MUTED,
            tooltip="关闭提示",
            width=28,
            height=28,
            on_click=lambda _e: self.hide(),
        )
        self.visible = False
        self.bgcolor = c.ERROR_SURFACE
        self.border = ft.Border.all(width=1, color=c.ERROR)
        self.border_radius = r.MD
        self.padding = ft.Padding.only(left=sp.MD, right=sp.XS, top=2, bottom=2)
        self.margin = ft.Margin.only(left=sp.LG, right=sp.LG, top=sp.SM, bottom=0)
        self.content = ft.Row(
            [
                ft.Icon(ft.Icons.VOLUME_OFF_ROUNDED, size=15, color=c.ERROR),
                self._text,
                self._close_btn,
            ],
            spacing=sp.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def show(self, message: str) -> None:
        """显示提示（重复调用覆盖文案）。"""
        self._text.value = message
        self.visible = True
        try:
            self.update()
        except Exception:  # noqa: BLE001 - 未挂载时忽略
            pass

    def hide(self) -> None:
        self.visible = False
        try:
            self.update()
        except Exception:  # noqa: BLE001
            pass
