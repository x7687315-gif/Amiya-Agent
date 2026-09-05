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


def format_day_compact(day: str) -> str:
    """窄幅用的紧凑格式："2026-08-16" → "9月6日 · 周日"（省年份）。

    P3：完整格式 ~150px，聊天区一窄 Dropdown 最先被裁（"2026" 被吃成 "026"）。
    年份信息不丢——完整格式挂在 Dropdown 的 tooltip 上。
    """
    try:
        d = datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        return day
    return f"{d.month}月{d.day}日 · 周{_WEEKDAY_ZH[d.weekday()]}"


# 聊天区（中间栏）宽度低于该值时切换紧凑形态：日期省年份 + 「回到今天」退化为纯图标
COMPACT_WIDTH = 480


class DateNav(ft.Container):
    """日期导航条控件。"""

    def __init__(self, on_day_change: Callable[[str], None], today: str) -> None:
        super().__init__()
        self._on_day_change = on_day_change
        self._today = today
        self._current = today
        self._days: List[str] = [today]
        self._narrow = False  # P3：窄幅紧凑形态（省年份 + 图标化「回到今天」）

        self._dropdown = ft.Dropdown(
            options=self._build_options(),
            value=today,
            on_select=self._on_dropdown_change,  # Flet 0.86 Dropdown 用 on_select
            dense=True,
            border=ft.InputBorder.NONE,
            text_size=13,
            content_padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=4, bottom=4),
            expand=True,
            # P3：紧凑格式省掉了年份，完整「2026年9月6日 · 周日」放 tooltip 兜底
            tooltip=format_day(today),
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
        # P3：窄幅时「回到今天」退化为纯图标（省 ~90px，语义由 tooltip 承担）
        self._today_btn_icon = ft.IconButton(
            icon=ft.Icons.EVENT_AVAILABLE_ROUNDED,
            icon_size=18,
            icon_color=c.PRIMARY,
            tooltip="回到今天",
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
                self._today_btn_icon,
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._sync_buttons()

    def set_width(self, width: float) -> None:
        """由 app 层随聊天区宽度同步调用，切换紧凑/完整形态（P3）。"""
        try:
            width = float(width or 0)
        except (TypeError, ValueError):
            return
        narrow = 0 < width < COMPACT_WIDTH
        if narrow == self._narrow:
            return
        self._narrow = narrow
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
        # P3：选项文本用紧凑格式（省年份）——完整格式的年份由 Dropdown 的
        # tooltip 承担；展开列表项也保持紧凑，避免把行宽顶爆。
        return [
            ft.dropdown.Option(
                key=d,
                text=format_day_compact(d) + ("（今天）" if d == self._today else ""),
            )
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
        show_today = self._current != self._today
        # P3：窄幅只留图标版，宽幅只留文字版（两者互斥，避免语义重复）
        self._today_btn.visible = show_today and not self._narrow
        self._today_btn_icon.visible = show_today and self._narrow
        # 紧凑格式省掉了年份，tooltip 用完整格式补全
        self._dropdown.tooltip = format_day(self._current)
        for btn in (self._prev_btn, self._next_btn, self._today_btn, self._today_btn_icon):
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
