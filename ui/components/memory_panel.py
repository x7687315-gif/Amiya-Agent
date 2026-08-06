"""右栏：记忆档案面板（最大亮点，长期关系的外显）。

三个分区：长期记忆（标签 + 置信星标）、近期事件（时间线）、重要目标（列表）。
v2 数据为占位/导入数据，结构先行；Phase 2 记忆层落地后接真实数据。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import flet as ft

from ui.theme import c, t, sp, r, layout


class MemoryPanel(ft.Container):
    """右侧记忆档案面板。"""

    def __init__(self, persona) -> None:
        super().__init__()
        self.persona = persona
        self._long_term: List[Tuple[str, int]] = [
            ("喜欢项目驱动学习", 5),
            ("在意用户的身体与情绪", 4),
            ("对本地使命坚定", 5),
        ]  # 占位（Phase 2 接 Memory 层）
        self._events: List[Tuple[str, str]] = [
            ("8月5日", "完成助手架构重构"),
            ("8月4日", "合并旧项目到 assistant-agent"),
            ("8月1日", "确立 RAG + Memory 架构"),
        ]  # 占位
        self._goals: List[str] = [
            "完成长期陪伴 AI Agent",
            "接回游戏原声语音",
        ]  # 占位

        self._long_term_col = ft.Column(spacing=sp.SM)
        self._events_col = ft.Column(spacing=sp.SM)
        self._goals_col = ft.Column(spacing=sp.SM)

        self._build()
        self._render_all()

    def _build(self) -> None:
        self.width = layout.RIGHT_COL_WIDTH
        self.bgcolor = c.SURFACE
        self.border = ft.Border.only(left=ft.BorderSide(width=1, color=c.BORDER))
        self.padding = ft.Padding.only(left=sp.LG, right=sp.LG, top=sp.LG, bottom=sp.LG)
        self.content = ft.Column(
            [
                ft.Text("记忆档案", color=c.TEXT_PRIMARY, size=t.TITLE, weight=ft.FontWeight.BOLD),
                ft.Container(height=sp.XS),
                self._section("长期记忆", self._long_term_col),
                self._section("近期事件", self._events_col),
                self._section("重要目标", self._goals_col),
            ],
            spacing=sp.LG,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _section(self, title: str, body: ft.Control) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, color=c.PRIMARY, size=t.TINY, weight=ft.FontWeight.BOLD),
                    body,
                ],
                spacing=sp.SM,
            ),
            bgcolor=c.PRIMARY_SOFT,
            border_radius=r.MD,
            padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.MD, bottom=sp.MD),
        )

    def _render_all(self) -> None:
        self._render_long_term()
        self._render_events()
        self._render_goals()

    def _render_long_term(self) -> None:
        self._long_term_col.controls = [
            self._hoverable(
                ft.Row(
                    [
                        ft.Text(label, color=c.TEXT_PRIMARY, size=t.CAPTION, expand=True),
                        ft.Text(self._stars(stars), color=c.PRIMARY, size=t.CAPTION),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
            for label, stars in self._long_term
        ]

    def _render_events(self) -> None:
        self._events_col.controls = [
            self._hoverable(
                ft.Row(
                    [
                        ft.Container(width=4, height=4, bgcolor=c.BRIDGE_ACCENT, border_radius=r.FULL, margin=ft.Margin.only(top=6)),
                        ft.Text(date, color=c.TEXT_MUTED, size=t.TINY, width=48),
                        ft.Text(text, color=c.TEXT_SECONDARY, size=t.CAPTION, expand=True),
                    ],
                    spacing=sp.SM,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                )
            )
            for date, text in self._events
        ]

    def _render_goals(self) -> None:
        self._goals_col.controls = [
            self._hoverable(
                ft.Row(
                    [
                        ft.Icon(ft.Icons.FLAG_ROUNDED, color=c.PRIMARY, size=14),
                        ft.Text(goal, color=c.TEXT_SECONDARY, size=t.CAPTION, expand=True),
                    ],
                    spacing=sp.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )
            for goal in self._goals
        ]

    @staticmethod
    def _stars(n: int) -> str:
        n = max(0, min(5, int(n)))
        return "★" * n + "☆" * (5 - n)

    @staticmethod
    def _hoverable(content: ft.Control) -> ft.Container:
        container = ft.Container(
            content=content,
            padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=sp.XS, bottom=sp.XS),
            border_radius=r.SM,
        )

        def _on_hover(e: ft.ControlEvent) -> None:
            container.bgcolor = c.SURFACE_SECONDARY if e.data == "true" else None
            container.update()

        container.on_hover = _on_hover
        return container

    # —— 对外更新接口（后续接 Memory 层）——
    def set_long_term(self, items: List[Tuple[str, int]]) -> None:
        self._long_term = list(items)
        self._render_long_term()
        self._long_term_col.update()

    def set_events(self, items: List[Tuple[str, str]]) -> None:
        self._events = list(items)
        self._render_events()
        self._events_col.update()

    def set_goals(self, items: List[str]) -> None:
        self._goals = list(items)
        self._render_goals()
        self._goals_col.update()
