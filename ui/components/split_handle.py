"""可拖拽的栏宽调节手柄：竖条，夹在左栏/聊天区/右栏之间。

用户需求（2026-08-17）：聊天框尺寸能手动调节、移动到合适位置——
壁纸左置后人物可能被聊天栏遮挡，拖手柄即可自行让位。

- 拖动时 on_resize(delta_x) 增量回调（持有者负责 clamp 与宽度应用）
- 松手 on_done()（持久化布局）
- 悬停高亮 + RESIZE_COLUMN 光标，宽度 9px 不抢空间
"""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from ui.theme import c, r


class SplitHandle(ft.GestureDetector):
    """栏宽调节手柄（纯 UI，不知道两侧是什么面板）。"""

    def __init__(
        self,
        on_resize: Callable[[float], None],
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        self._on_resize = on_resize
        self._on_done = on_done
        self._bar = ft.Container(width=9, bgcolor=None, border_radius=r.FULL)
        super().__init__(
            content=self._bar,
            mouse_cursor=ft.MouseCursor.RESIZE_COLUMN,
            drag_interval=10,
            on_horizontal_drag_update=self._handle_update,
            on_horizontal_drag_end=self._handle_done,
            on_horizontal_drag_cancel=self._handle_done,
            on_hover=self._hover,
        )

    def _handle_update(self, e: ft.ControlEvent) -> None:
        delta = getattr(e, "primary_delta", None)
        if delta is None:
            delta = getattr(getattr(e, "local_delta", None), "x", 0) or 0
        try:
            self._on_resize(float(delta))
        except Exception:  # noqa: BLE001 - 拖拽异常不冒泡
            pass

    def _handle_done(self, _e: ft.ControlEvent) -> None:
        if self._on_done is not None:
            try:
                self._on_done()
            except Exception:  # noqa: BLE001
                pass

    def _hover(self, e: ft.ControlEvent) -> None:
        self._bar.bgcolor = c.PRIMARY_LIGHT if getattr(e, "data", "") == "true" else None
        try:
            self._bar.update()
        except Exception:  # noqa: BLE001 - 未挂载时忽略
            pass
