"""中栏：思考态 + RAG 检索进度可视化。

当助手处理用户消息时，展示「整理话语 → 检索记忆 → 应用规则」的过程，
把 RAG 检索外显，体现 Agent 能力。阶段由 core/agent.py 的 on_phase 回调驱动；
RAG 未接入前由 Agent 发出模拟阶段序列（带短暂延迟），保证视觉完整。
"""
from __future__ import annotations

import flet as ft

from ui.theme import c, t, sp, r, layout
from ui.components.avatar import make_avatar
from ui.design.avatar_provider import AvatarProvider

# 检索清单项（对应设计稿）
_RETRIEVAL_ITEMS = ["长期记忆", "最近事件", "Persona 规则"]

# 阶段 → (状态文案, 已完成的检索项)
_PHASES = {
    "retrieving": ("助手正在检索你的记忆…", ["长期记忆"]),
    "reasoning": ("助手正在整理思路…", ["长期记忆", "最近事件", "Persona 规则"]),
}


class ThinkingOverlay(ft.Row):
    """思考态覆盖层（替代旧的三点指示器）。"""

    def __init__(self, avatar_provider: AvatarProvider | None = None) -> None:
        super().__init__()
        self.spacing = sp.SM
        self.vertical_alignment = ft.CrossAxisAlignment.START
        self._status_text = ft.Text(
            "助手正在整理你的话……",
            color=c.TEXT_SECONDARY,
            size=t.CAPTION,
            italic=True,
        )
        self._checks = [self._check_item(name) for name in _RETRIEVAL_ITEMS]
        self.controls = [
            make_avatar(avatar_provider, state_key="thinking", radius=16, text_size=13),
            ft.Column(
                [self._status_text, ft.Column(self._checks, spacing=sp.XS)],
                spacing=sp.SM,
            ),
        ]

    def _check_item(self, name: str) -> ft.Row:
        return ft.Row(
            [
                ft.Text("○", color=c.TEXT_MUTED, size=t.TINY),
                ft.Text(name, color=c.TEXT_MUTED, size=t.TINY),
            ],
            spacing=sp.XS,
        )

    def set_phase(self, phase: str) -> None:
        """根据 Agent 阶段事件更新文案与勾选状态。"""
        if phase not in _PHASES:
            return
        text, done = _PHASES[phase]
        self._status_text.value = text
        self._status_text.update()
        done_set = set(done)
        for row, name in zip(self._checks, _RETRIEVAL_ITEMS):
            mark, label = row.controls
            is_done = name in done_set
            mark.value = "✓" if is_done else "○"
            mark.color = c.PRIMARY if is_done else c.TEXT_MUTED
            label.color = c.TEXT_PRIMARY if is_done else c.TEXT_MUTED
        self.update()
