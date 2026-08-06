"""Design Token 单元测试：确认色值/常量集中、无遗漏。"""
from __future__ import annotations

from ui.theme import c, t, sp, r, layout, anim, EMOTIONS, DEFAULT_EMOTION


def test_color_tokens():
    assert c.PRIMARY == "#7C6BC4"
    assert c.ON_PRIMARY == "#FFFFFF"  # 头像/发送键白色文字
    assert c.SHADOW.startswith("rgba")
    # v2 新增：情绪状态色 / 信赖度 / 舰桥强调
    assert c.STATE_CALM == "#8FBF9F"
    assert c.STATE_THINKING == "#7C6BC4"
    assert c.STATE_WORRIED == "#E0A458"
    assert c.STATE_HAPPY == "#E58FA8"
    assert c.TRUST_LOW == "#C9C3E0"
    assert c.TRUST_HIGH == "#7C6BC4"
    assert c.BRIDGE_ACCENT == "#7FB8C4"


def test_layout_constants():
    assert layout.INPUT_MAX_LENGTH == 2000
    assert layout.SCROLL_DURATION == 200
    assert layout.SCROLL_DURATION_FAST == 100
    assert layout.BUBBLE_TAIL == 4
    assert layout.HEADER_PAD_Y == 10
    # v2 三栏窗口尺寸
    assert layout.WINDOW_WIDTH == 1180
    assert layout.WINDOW_HEIGHT == 760
    assert layout.WINDOW_MIN_WIDTH == 960
    assert layout.WINDOW_MIN_HEIGHT == 640
    assert layout.LEFT_COL_WIDTH == 260
    assert layout.RIGHT_COL_WIDTH == 300


def test_typography_spacing_radius():
    assert t.BODY == 14
    assert sp.LG == 16
    assert r.MD == 12
    assert anim.EASE_OUT == "easeOut"
    # v2 动效常量
    assert anim.STAGGER == 80
    assert anim.PULSE_MS == 1200
    assert anim.BREATH_MS == 8000


def test_emotion_states():
    assert set(EMOTIONS.keys()) == {"calm", "thinking", "worried", "happy"}
    assert EMOTIONS["calm"].emoji == "🌱"
    assert EMOTIONS["thinking"].label == "思考"
    assert EMOTIONS["calm"].color == c.STATE_CALM
    assert DEFAULT_EMOTION == "calm"

