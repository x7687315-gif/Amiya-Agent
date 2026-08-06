"""UI 组件单元测试：构造、工厂产出、关键交互逻辑（离线，无需 page）。"""
from __future__ import annotations

import flet as ft
import pytest

from core.persona import load_persona
from ui.components.chat_bubble import ChatBubble
from ui.components.avatar import make_avatar, make_user_avatar
from ui.components.header import Header
from ui.components.chat_area import ChatArea
from ui.components.input_bar import InputBar
from ui.components.persona_drawer import PersonaDrawer
from ui.components.empty_state import EmptyState
from ui.components.thinking_indicator import ThinkingIndicator
from ui.components.persona_status import PersonaStatusPanel
from ui.components.memory_panel import MemoryPanel
from ui.components.thinking_overlay import ThinkingOverlay
from ui.design.avatar_provider import TextAvatarProvider, ImageAvatarProvider
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
    PersonaStatusPanel(persona)
    MemoryPanel(persona)
    ThinkingOverlay()


def test_chat_bubble_factories():
    assert isinstance(ChatBubble.user("hi"), ft.Control)
    assert isinstance(ChatBubble.assistant("yo"), ft.Control)
    assert isinstance(ChatBubble.error("err"), ft.Control)


def test_make_avatar():
    av = make_avatar(None, state_key="calm", radius=36, text_size=24, bgcolor=c.PRIMARY_LIGHT, text_color=c.PRIMARY)
    assert isinstance(av, ft.CircleAvatar)
    assert av.bgcolor == c.PRIMARY_LIGHT
    assert av.content.color == c.PRIMARY


def test_make_user_avatar():
    av = make_user_avatar(radius=20)
    assert isinstance(av, ft.CircleAvatar)
    assert av.bgcolor == c.SURFACE_SECONDARY
    assert av.content.value == "博"


def test_avatar_provider_text_and_fallback():
    provider = TextAvatarProvider(text="阿")
    av = provider.get(state_key="thinking", radius=36)
    assert isinstance(av, ft.CircleAvatar)
    assert av.content.value == "阿"

    # 图片提供方在文件缺失时回退到文字头像
    img_provider = ImageAvatarProvider(folder="/nonexistent/path", fallback=provider)
    fallback_av = img_provider.get(state_key="happy", radius=36)
    assert isinstance(fallback_av, ft.CircleAvatar)
    assert fallback_av.content.value == "阿"


def test_header_set_thinking(persona):
    h = Header(persona)
    h.set_thinking(True)
    assert h._status_text.value == "正在思考…"
    assert isinstance(h._status_dot, ft.ProgressRing)
    h.set_thinking(False)
    assert h._status_text.value == "在线"
    assert isinstance(h._status_dot, ft.Container)


def test_persona_status_panel(persona):
    panel = PersonaStatusPanel(persona)
    panel.set_state("thinking")
    assert panel._state_label.value == "思考"
    assert panel._state_dot.bgcolor == c.STATE_THINKING
    panel.set_trust(85)
    assert panel._trust_bar.value == 0.85
    assert panel._trust_label.value == "85%"
    panel.set_companionship_minutes(23)
    assert "23" in panel._companionship_label.value
    panel.set_recent_memories(["事件A", "事件B"])
    assert panel._recent == ["事件A", "事件B"]


def test_memory_panel(persona):
    from core.memory import MemoryManager, SQLiteMemoryStore

    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    mgr.remember("fact", "用户喜欢爬山", importance=6)
    mgr.remember("goal", "想学吉他", importance=5)
    mgr.remember("event", "今天去了公园", importance=4)
    mgr.propose("preference", "爱喝咖啡", reason="提过几次")

    panel = MemoryPanel(persona, memory=mgr)
    # 三个分区各至少 1 条记忆 + 1 条待确认候选
    assert len(panel._long_term_col.controls) >= 1
    assert len(panel._goals_col.controls) >= 1
    assert len(panel._events_col.controls) >= 1
    assert len(panel._cand_col.controls) >= 1

    # 记忆功能未启用时只显示一条空态提示
    disabled = MemoryPanel(persona, memory=None)
    assert len(disabled._long_term_col.controls) == 1


def test_thinking_overlay_phases():
    ov = ThinkingOverlay()
    ov.set_phase("retrieving")
    assert "检索" in ov._status_text.value
    # retrieving 阶段仅「长期记忆」完成
    assert ov._checks[0].controls[0].value == "✓"
    assert ov._checks[1].controls[0].value == "○"

    ov.set_phase("reasoning")
    assert "整理" in ov._status_text.value
    # reasoning 阶段三项全部完成
    assert all(row.controls[0].value == "✓" for row in ov._checks)


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
