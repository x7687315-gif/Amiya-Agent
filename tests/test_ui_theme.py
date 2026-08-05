"""Design Token 单元测试：确认色值/常量集中、无遗漏。"""
from __future__ import annotations

from ui.theme import c, t, sp, r, layout, anim


def test_color_tokens():
    assert c.PRIMARY == "#7C6BC4"
    assert c.ON_PRIMARY == "#FFFFFF"  # 头像/发送键白色文字
    assert c.SHADOW.startswith("rgba")


def test_layout_constants():
    assert layout.INPUT_MAX_LENGTH == 2000
    assert layout.SCROLL_DURATION == 200
    assert layout.SCROLL_DURATION_FAST == 100
    assert layout.BUBBLE_TAIL == 4
    assert layout.HEADER_PAD_Y == 10
    assert layout.WINDOW_WIDTH == 520
    assert layout.WINDOW_HEIGHT == 800


def test_typography_spacing_radius():
    assert t.BODY == 14
    assert sp.LG == 16
    assert r.MD == 12
    assert anim.EASE_OUT == "easeOut"
