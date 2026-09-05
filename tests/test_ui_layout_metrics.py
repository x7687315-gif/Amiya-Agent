"""布局度量回归测试（docs/20_UI_ADAPTATION_ISSUES.md 的 P1 / P2 / P7 验收口径）。

这些测试守的是**纯计算**，不需要 Flet 运行时：
- P2 动态钳制：两侧无论怎么拖 / 恢复持久化值，聊天区（中间栏）恒 ≥ CHAT_MIN_WIDTH；
- P1 气泡上限：``max_width_ratio`` 不再是死参数，且窄幅下不因头像/朗读键溢出；
- P7 让位边距：窄幅自动收窄，宽幅封顶回 64。
"""
from __future__ import annotations

import flet as ft

from ui.components.chat_bubble import ChatBubble
from ui.layout_metrics import (
    CHAT_MIN_WIDTH,
    LEFT_MIN,
    RIGHT_MIN,
    bubble_max_width,
    bubble_side_margin,
    chat_column_width,
    chat_text_width,
    clamp_left_width,
    clamp_pair,
    clamp_right_width,
)
from ui.theme import layout

# docs/20 记录的用户实际拖拽位：窗口 1180、左 180 / 右 567 → 聊天区 415
DOC_LEFT, DOC_RIGHT, DOC_WINDOW = 180, 567, 1180


# ---------- 基线（重构前后必须一致的量） ----------

def test_default_layout_chat_width():
    """默认布局：1180 − 260 − 300 − 2×9 = 602；文本区再扣 32。"""
    assert chat_column_width(1180, 260, 300) == 602
    assert chat_text_width(1180, 260, 300) == 570


def test_doc_scenario_reproduces_415():
    """复现 docs/20 的 415px：证明公式与现场吻合（之后才谈得上修复）。"""
    assert chat_column_width(DOC_WINDOW, DOC_LEFT, DOC_RIGHT) == 415


# ---------- P2 动态钳制 ----------

def test_p2_chat_not_squeezed_by_left_drag():
    """最小窗口下把左栏拖向静态上限，聊天区仍 ≥ 保底。"""
    window = 960  # WINDOW_MIN_WIDTH
    left = clamp_left_width(560, right_width=300, window_width=window)
    assert chat_column_width(window, left, 300) >= CHAT_MIN_WIDTH


def test_p2_chat_not_squeezed_by_right_drag():
    window = 960
    right = clamp_right_width(620, left_width=300, window_width=window)
    assert chat_column_width(window, 300, right) >= CHAT_MIN_WIDTH


def test_p2_persisted_invalid_layout_revalidated():
    """docs/20 现场（180/567，聊天区 415 < 保底）重校后必须 ≥ 保底。"""
    left, right = clamp_pair(DOC_LEFT, DOC_RIGHT, DOC_WINDOW)
    assert chat_column_width(DOC_WINDOW, left, right) >= CHAT_MIN_WIDTH


def test_p2_both_maxed_then_window_shrinks():
    """换小显示器：旧布局（两侧接近旧上限）也要收敛到合法。"""
    left, right = clamp_pair(560, 620, 960)
    assert chat_column_width(960, left, right) >= CHAT_MIN_WIDTH


def test_p2_mutual_clamp_same_order_as_resize_handler():
    """resize 路径（先钳左、再以新左钳右）同样守不变量。"""
    window = 960
    left, right = 482, 482  # 在 1180 窗口下合法，缩到 960 后非法
    left = clamp_left_width(left, right, window)
    right = clamp_right_width(right, left, window)
    assert chat_column_width(window, left, right) >= CHAT_MIN_WIDTH


def test_p2_panel_floors_respected():
    """钳制不会把面板压塌：两侧至少保住各自下限。"""
    left, right = clamp_pair(0, 0, 1180)
    assert left == LEFT_MIN
    assert right == RIGHT_MIN


# ---------- P1 气泡上限 ----------

def test_p1_ratio_honored_at_normal_width():
    tw = 570
    assert bubble_max_width(tw, layout.AI_BUBBLE_MAX_RATIO) == int(
        tw * layout.AI_BUBBLE_MAX_RATIO
    )


def test_p1_reserved_prevents_row_overflow():
    """窄幅下（头像 32 + 朗读键 40 + spacing 24 + margin 8 = 104）会让位。"""
    tw = 388  # 聊天区保底 420 对应的文本宽
    cap = bubble_max_width(tw, layout.AI_BUBBLE_MAX_RATIO, reserved=104)
    assert cap == tw - 104  # ratio×宽(302) > 剩余空间(284)，取剩余


def test_p1_unknown_width_is_unconstrained():
    """available_width=0（未知）→ 不设上限，保持向后兼容。"""
    assert bubble_max_width(0, layout.AI_BUBBLE_MAX_RATIO) == 0


def test_p1_dead_param_now_used():
    """P1 核心：``max_width_ratio`` 曾在签名里声明但从未使用——现在必须真实
    落到气泡外层容器的 width 上。"""
    tw = 570
    user = ChatBubble.user("hi", available_width=tw)
    assert getattr(user, "_bubble_cap").width == bubble_max_width(
        tw, layout.USER_BUBBLE_MAX_RATIO, reserved=bubble_side_margin(tw) + 8
    )

    ai = ChatBubble.assistant("yo", available_width=tw)
    assert getattr(ai, "_bubble_cap").width == bubble_max_width(
        tw, layout.AI_BUBBLE_MAX_RATIO, reserved=104
    )


def test_p1_no_more_expand_full_row():
    """P1：有朗读键时气泡不再 expand 撑满整行（改由弹性占位推 🔊 到行尾）。"""
    holder = ChatBubble.assistant("yo", on_speak=lambda t: None, available_width=570)
    cap = getattr(holder, "_bubble_cap")
    assert not cap.expand
    assert any(isinstance(c, ft.Container) and c.expand for c in holder.controls)


def test_p1_bubble_caps_updatable_in_place():
    """宽度变化走「回写容器」而非重建气泡（保住流式输出与 🔊 订阅）。"""
    holder = ChatBubble.user("hi", available_width=570)
    ChatBubble.apply_width(holder, 300)
    cap = getattr(holder, "_bubble_cap")
    expected = bubble_max_width(300, layout.USER_BUBBLE_MAX_RATIO, reserved=bubble_side_margin(300) + 8)
    assert cap.width == expected
    assert cap.margin.left == bubble_side_margin(300)


# ---------- P7 让位边距 ----------

def test_p7_margin_shrinks_when_narrow():
    assert bubble_side_margin(388) == int(388 * 0.08)
    assert bubble_side_margin(570) == int(570 * 0.08)


def test_p7_margin_capped_when_wide():
    assert bubble_side_margin(1200) == 64


def test_p7_margin_unknown_width_falls_back():
    assert bubble_side_margin(0) == 64
