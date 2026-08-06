"""左栏：角色状态面板（Presence 主战场）。

展示助手的在场证据：头像与身份、当前情绪状态、信赖度、今日陪伴时长、最近记忆。
v2 数据为占位/规则启发式，结构先行；后续接 PersonaRuntime / Memory 层即替换数据源。
"""
from __future__ import annotations

from typing import List

import flet as ft

from ui.theme import c, t, sp, r, layout, anim, EMOTIONS, DEFAULT_EMOTION
from ui.components.avatar import make_avatar
from ui.design.avatar_provider import AvatarProvider


class PersonaStatusPanel(ft.Container):
    """左侧角色状态栏。"""

    def __init__(self, persona, avatar_provider: AvatarProvider | None = None) -> None:
        super().__init__()
        self.persona = persona
        self._avatar_provider = avatar_provider
        self._state_key = DEFAULT_EMOTION
        self._trust = 70  # 占位信赖度（Phase 2 接 PersonaRuntime）
        self._companionship_minutes = 0
        self._recent: List[str] = [
            "用户完成了助手架构设计",
            "正在调整作息",
            "计划香港旅行",
        ]  # 占位最近记忆（Phase 2 接 Memory 层）

        self._state_emoji = ft.Text("", size=22)
        self._state_label = ft.Text("", color=c.TEXT_PRIMARY, size=t.CAPTION, weight=ft.FontWeight.W_500)
        self._state_dot = ft.Container(width=8, height=8, border_radius=r.FULL, bgcolor=c.STATE_CALM)
        self._trust_bar = ft.ProgressBar(
            value=self._trust / 100, color=c.TRUST_HIGH, bgcolor=c.TRUST_LOW, height=8
        )
        self._trust_label = ft.Text(f"{self._trust}%", color=c.TEXT_SECONDARY, size=t.TINY)
        self._companionship_label = ft.Text(
            "已经陪伴 0 分钟", color=c.TEXT_SECONDARY, size=t.CAPTION
        )
        self._recent_col = ft.Column(spacing=sp.SM)

        self._build()
        self.set_state(self._state_key)  # 初始化状态显示

    def _build(self) -> None:
        self.width = layout.LEFT_COL_WIDTH
        self.bgcolor = c.SURFACE
        self.border = ft.Border.only(right=ft.BorderSide(width=1, color=c.BORDER))
        self.padding = ft.Padding.only(left=sp.LG, right=sp.LG, top=sp.LG, bottom=sp.LG)

        identity = ft.Row(
            [
                make_avatar(self._avatar_provider, state_key="calm", radius=26, text_size=18),
                ft.Column(
                    [
                        ft.Text(self.persona.name, color=c.TEXT_PRIMARY, size=t.TITLE, weight=ft.FontWeight.BOLD),
                        ft.Text(self.persona.title or "本地", color=c.TEXT_SECONDARY, size=t.TINY),
                    ],
                    spacing=2,
                    expand=True,
                ),
            ],
            spacing=sp.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        self.content = ft.Column(
            [
                identity,
                self._section_state(),
                self._section_trust(),
                self._section_companionship(),
                self._section_recent(),
            ],
            spacing=sp.LG,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _card(self, children: List[ft.Control]) -> ft.Container:
        return ft.Container(
            content=ft.Column(children, spacing=sp.SM),
            bgcolor=c.PRIMARY_SOFT,
            border_radius=r.MD,
            padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.MD, bottom=sp.MD),
        )

    def _section_state(self) -> ft.Container:
        return self._card(
            [
                ft.Text("当前状态", color=c.TEXT_MUTED, size=t.TINY, weight=ft.FontWeight.BOLD),
                ft.Row(
                    [self._state_dot, self._state_emoji, self._state_label],
                    spacing=sp.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ]
        )

    def _section_trust(self) -> ft.Container:
        return self._card(
            [
                ft.Row(
                    [
                        ft.Text("信赖度", color=c.TEXT_MUTED, size=t.TINY, weight=ft.FontWeight.BOLD),
                        ft.Container(expand=True),
                        self._trust_label,
                    ],
                ),
                self._trust_bar,
            ]
        )

    def _section_companionship(self) -> ft.Container:
        return self._card(
            [
                ft.Text("今日陪伴", color=c.TEXT_MUTED, size=t.TINY, weight=ft.FontWeight.BOLD),
                self._companionship_label,
            ]
        )

    def _section_recent(self) -> ft.Container:
        self._render_recent()
        return self._card(
            [
                ft.Text("最近记忆", color=c.TEXT_MUTED, size=t.TINY, weight=ft.FontWeight.BOLD),
                self._recent_col,
            ]
        )

    def _render_recent(self) -> None:
        self._recent_col.controls = [
            ft.Row(
                [
                    ft.Container(width=4, height=4, bgcolor=c.PRIMARY, border_radius=r.FULL, margin=ft.Margin.only(top=6)),
                    ft.Text(item, color=c.TEXT_SECONDARY, size=t.CAPTION, expand=True),
                ],
                spacing=sp.SM,
                alignment=ft.MainAxisAlignment.START,
            )
            for item in self._recent
        ]

    # —— 对外更新接口（后续接真实数据源）——
    @staticmethod
    def _safe_update(ctrl: ft.Control) -> None:
        """控件未挂载到 page 时 update() 会抛 RuntimeError，安全忽略。"""
        try:
            ctrl.update()
        except RuntimeError:
            pass

    def set_state(self, state_key: str) -> None:
        """切换情绪状态（calm/thinking/worried/happy）。"""
        if state_key not in EMOTIONS:
            state_key = DEFAULT_EMOTION
        self._state_key = state_key
        emo = EMOTIONS[state_key]
        self._state_emoji.value = emo.emoji
        self._state_label.value = emo.label
        self._state_dot.bgcolor = emo.color
        self._safe_update(self._state_dot)
        self._safe_update(self._state_emoji)
        self._safe_update(self._state_label)

    def set_trust(self, value: int) -> None:
        """设置信赖度（0–100）。"""
        self._trust = max(0, min(100, int(value)))
        self._trust_bar.value = self._trust / 100
        self._trust_label.value = f"{self._trust}%"
        self._safe_update(self._trust_bar)
        self._safe_update(self._trust_label)

    def set_companionship_minutes(self, minutes: int) -> None:
        self._companionship_minutes = max(0, int(minutes))
        self._companionship_label.value = f"已经陪伴 {self._companionship_minutes} 分钟"
        self._safe_update(self._companionship_label)

    def set_recent_memories(self, items: List[str]) -> None:
        self._recent = list(items)
        self._render_recent()
        self._safe_update(self._recent_col)
