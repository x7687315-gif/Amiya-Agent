"""UI 组件单元测试：构造、工厂产出、关键交互逻辑（离线，无需 page）。"""
from __future__ import annotations

import flet as ft
import pytest

from core.persona import load_persona
from ui.components.chat_bubble import ChatBubble
from ui.components.avatar import make_avatar
from ui.components.header import Header
from ui.components.chat_area import ChatArea
from ui.components.input_bar import InputBar
from ui.components.persona_drawer import PersonaDrawer
from ui.components.empty_state import EmptyState
from ui.components.thinking_indicator import ThinkingIndicator
from ui.theme import c


@pytest.fixture(scope="module")
def persona():
    return load_persona()


def test_components_construct(persona):
    Header(persona)
    ChatArea(persona)
    InputBar(on_send=lambda t: None)
    PersonaDrawer(persona)
    EmptyState(persona)
    ThinkingIndicator()


def test_chat_bubble_factories():
    assert isinstance(ChatBubble.user("hi"), ft.Control)
    assert isinstance(ChatBubble.assistant("yo"), ft.Control)
    assert isinstance(ChatBubble.error("err"), ft.Control)


def test_make_avatar():
    av = make_avatar("阿", 36, c.PRIMARY_LIGHT, text_color=c.PRIMARY, text_size=24)
    assert isinstance(av, ft.CircleAvatar)
    assert av.bgcolor == c.PRIMARY_LIGHT
    assert av.content.color == c.PRIMARY


def test_header_set_thinking(persona):
    h = Header(persona)
    h.set_thinking(True)
    assert h._status_text.value == "正在思考…"
    assert isinstance(h._status_dot, ft.ProgressRing)
    h.set_thinking(False)
    assert h._status_text.value == "在线"
    assert isinstance(h._status_dot, ft.Container)


def test_chat_area_add_and_scroll_pause(persona):
    ca = ChatArea(persona)
    ca.add_user("用户好")
    tc = ca.start_assistant()
    ca.append_assistant_text(tc, "我")
    ca.append_assistant_text(tc, "在。")
    ca.add_error("干扰")

    # 模拟用户向上滚动 -> 暂停自动回底 + 显示"新消息"按钮
    ca._on_scroll(type("E", (), {"pixels": 0, "max_scroll_extent": 800})())
    assert ca._auto_scroll is False and ca._scroll_btn.visible is True

    # 点击按钮回底 -> 恢复自动回底 + 隐藏按钮
    ca._scroll_to_bottom(None)
    assert ca._auto_scroll is True and ca._scroll_btn.visible is False
