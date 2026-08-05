"""聊天区组件：消息列表、空态、错误提示、滚动控制。"""
from __future__ import annotations

from typing import List

import flet as ft

from ui.theme import c, sp
from ui.components.chat_bubble import ChatBubble
from ui.components.thinking_indicator import ThinkingIndicator
from ui.components.empty_state import EmptyState


class ChatArea(ft.Container):
    """主聊天区，承载消息气泡与状态。"""

    def __init__(self, persona) -> None:
        super().__init__()
        self.persona = persona
        self._has_messages = False
        self._thinking_indicator: ft.Control | None = None
        self._empty_state = EmptyState(persona)
        self._list = ft.ListView(
            controls=[self._empty_state],
            spacing=sp.SM,
            padding=ft.Padding.only(left=sp.LG, right=sp.LG, top=sp.LG, bottom=sp.SM),
            auto_scroll=True,
            expand=True,
        )
        self.content = self._list
        self.bgcolor = c.BG
        self.expand = True

    def _ensure_message_list(self) -> None:
        """第一条消息加入时移除空态。"""
        if not self._has_messages:
            self._has_messages = True
            self._list.controls.clear()

    def add_user(self, text: str) -> None:
        """添加用户消息并显示思考指示器。"""
        self._ensure_message_list()
        self._list.controls.append(ChatBubble.user(text))
        self._thinking_indicator = ThinkingIndicator()
        self._list.controls.append(self._thinking_indicator)
        self._list.scroll_to(offset=-1, duration=200)
        self._list.update()

    def start_assistant(self) -> ft.Text:
        """开始助手回复，移除思考指示器并返回文本控件用于流式更新。"""
        self._remove_thinking()
        text_control = ft.Text(
            "",
            color=c.TEXT_PRIMARY,
            size=14,
            selectable=True,
        )
        self._list.controls.append(ChatBubble.assistant("", text_control=text_control))
        self._list.scroll_to(offset=-1, duration=200)
        self._list.update()
        return text_control

    def append_assistant_text(self, text_control: ft.Text, delta: str) -> None:
        """向助手文本控件追加流式内容。"""
        text_control.value += delta
        self._list.scroll_to(offset=-1, duration=100)
        text_control.update()

    def remove_thinking(self) -> None:
        """移除思考指示器（回复为空或失败时）。"""
        self._remove_thinking()

    def _remove_thinking(self) -> None:
        if self._thinking_indicator is not None and self._thinking_indicator in self._list.controls:
            self._list.controls.remove(self._thinking_indicator)
            self._thinking_indicator = None
            self._list.update()

    def add_assistant(self, text: str) -> None:
        """直接添加完整的助手消息（非流式或兜底）。"""
        self._ensure_message_list()
        self._remove_thinking()
        self._list.controls.append(ChatBubble.assistant(text))
        self._list.scroll_to(offset=-1, duration=200)
        self._list.update()

    def add_error(self, text: str) -> None:
        """添加系统错误提示。"""
        self._ensure_message_list()
        self._remove_thinking()
        self._list.controls.append(ChatBubble.error(text))
        self._list.scroll_to(offset=-1, duration=200)
        self._list.update()

    def clear(self) -> None:
        """清空聊天区，回到空态。"""
        self._has_messages = False
        self._thinking_indicator = None
        self._list.controls.clear()
        self._list.controls.append(self._empty_state)
        self._list.update()
