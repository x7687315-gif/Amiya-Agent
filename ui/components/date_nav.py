"""日期导航条：按天浏览历史对话（时间轴 UI）。

位于聊天区上方，提供：
- 日期下拉框：列出「有对话记录」的日期（新→旧），今天恒在列表里
- ‹ / › 前后翻页：在**有记录的日期**之间跳步（跳过空白天，走空日期没意义）
- 「回到今天」：一键返回交互模式

只负责发出 on_day_change(day) 事件，不关心数据从哪来、聊天区怎么渲染——
数据加载与只读切换由 app 层接线（UI → Agent/Memory 单向依赖红线不破）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Optional

import flet as ft

from ui.theme import c, sp, r

_WEEKDAY_ZH = "一二三四五六日"


def format_day(day: str) -> str:
    """"2026-08-16" → "2026年8月16日 · 周日"。非法输入原样返回。"""
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{d.year}年{d.month}月{d.day}日 · 周{_WEEKDAY_ZH[d.weekday()]}"


class DateNav(ft.Container):
    """日期导航条控件。"""

    def __init__(self, on_day_change: Callable[[str], None], today: str) -> None:
        super().__init__()
        self._on_day_change = on_day_change
        self._today = today
        self._current = today
        self._days: List[str] = [today]

        self._dropdown = ft.Dropdown(
            options=self._build_options(),
            value=today,
            on_select=self._on_dropdown_change,  # Flet 0.86 Dropdown 用 on_select
            dense=True,
            border=ft.InputBorder.NONE,
            text_size=13,
            content_padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=4, bottom=4),
            expand=True,
        )
        self._prev_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT_ROUNDED,
            icon_size=18,
            icon_color=c.TEXT_SECONDARY,
            tooltip="上一天（有记录的）",
            on_click=self._on_prev,
        )
        self._next_btn = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT_ROUNDED,
            icon_size=18,
            icon_color=c.TEXT_SECONDARY,
            tooltip="下一天（有记录的）",
            on_click=self._on_next,
        )
        self._today_btn = ft.TextButton(
            "回到今天",
            icon=ft.Icons.EVENT_AVAILABLE_ROUNDED,
            on_click=self._on_today_click,
            visible=False,
        )

        # 透明化：壁纸为全局统一底色
        self.bgcolor = None
        self.border = ft.Border.only(bottom=ft.BorderSide(width=1, color=c.BORDER))
        self.padding = ft.Padding.only(left=sp.SM, right=sp.SM, top=0, bottom=0)
        self.content = ft.Row(
            [
                ft.Icon(ft.Icons.CALENDAR_MONTH_ROUNDED, size=16, color=c.TEXT_MUTED),
                self._prev_btn,
                self._dropdown,
                self._next_btn,
                self._today_btn,
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._sync_buttons()

    # ----- 对外接口（app 层调用） -----
    def set_days(self, days: List[str]) -> None:
        """更新可选日期列表（'YYYY-MM-DD'，新→旧）。今天不存在时自动补上。"""
        self._days = list(days)
        if self._today not in self._days:
            self._days = [self._today] + self._days
        self._dropdown.options = self._build_options()
        self._dropdown.value = self._current if self._current in self._days else self._today
        self._current = self._dropdown.value
        try:
            self._dropdown.update()
        except Exception:  # noqa: BLE001 - 未挂载时忽略
            pass
        self._sync_buttons()

    def set_today(self, today: str) -> None:
        """刷新「今天」（应用跨天仍开着时由 app 每次调用）。"""
        self._today = today
        if today not in self._days:
            self.set_days(self._days)
        self._sync_buttons()

    def set_current(self, day: str) -> None:
        """外部（app）同步当前浏览日，不回调（避免事件环）。"""
        self._current = day
        self._dropdown.value = day if day in self._days else self._today
        try:
            self._dropdown.update()
        except Exception:  # noqa: BLE001
            pass
        self._sync_buttons()

    # ----- 内部 -----
    def _build_options(self) -> List[ft.dropdown.Option]:
        return [
            ft.dropdown.Option(key=d, text=format_day(d) + ("（今天）" if d == self._today else ""))
            for d in self._days
        ]

    def _sync_buttons(self) -> None:
        """按当前位置启停翻页键、显隐「回到今天」。"""
        try:
            idx = self._days.index(self._current)
        except ValueError:
            idx = self._days.index(self._today) if self._today in self._days else 0
            self._current = self._days[idx]
        self._prev_btn.disabled = idx >= len(self._days) - 1      # 列表新→旧：最旧在末尾
        self._next_btn.disabled = idx <= 0
        self._today_btn.visible = self._current != self._today
        for btn in (self._prev_btn, self._next_btn, self._today_btn):
            try:
                btn.update()
            except Exception:  # noqa: BLE001 - 未挂载时忽略
                pass

    def _emit(self, day: Optional[str]) -> None:
        if not day or day == self._current:
            return
        self.set_current(day)
        self._on_day_change(day)

    def _on_dropdown_change(self, e: ft.ControlEvent) -> None:
        self._emit(getattr(e, "control", None) and e.control.value or None)

    def _on_prev(self, _e: ft.ControlEvent) -> None:
        """列表新→旧：上一天 = 向列表尾部走一步。"""
        try:
            idx = self._days.index(self._current)
        except ValueError:
            return
        self._emit(self._days[idx + 1] if idx + 1 < len(self._days) else None)

    def _on_next(self, _e: ft.ControlEvent) -> None:
        try:
            idx = self._days.index(self._current)
        except ValueError:
            return
        self._emit(self._days[idx - 1] if idx - 1 >= 0 else None)

    def _on_today_click(self, _e: ft.ControlEvent) -> None:
        self._emit(self._today)
