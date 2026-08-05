"""Design Token：assistant-agent 视觉系统。

所有 UI 组件必须从此模块导入样式常量，禁止硬编码色值/字号/间距。
风格：浅色背景 + 几何极简主义 + 细描边 + 少阴影。
"""
from __future__ import annotations

from dataclasses import dataclass

import flet as ft


# 便捷对齐常量（Flet 0.86 不再提供 ft.alignment.center 等预设）
ALIGN_CENTER = ft.alignment.Alignment(0, 0)
ALIGN_CENTER_RIGHT = ft.alignment.Alignment(1, 0)
ALIGN_TOP_CENTER = ft.alignment.Alignment(0, -1)


@dataclass(frozen=True)
class Colors:
    """配色 Token。"""

    PRIMARY: str = "#7C6BC4"  # 助手紫（降饱和）
    PRIMARY_LIGHT: str = "#EDE9FA"  # 用户气泡、高亮
    PRIMARY_DARK: str = "#5E4FA3"  # Hover / Pressed
    PRIMARY_SOFT: str = "#F7F5FD"  # 淡紫背景
    ON_PRIMARY: str = "#FFFFFF"  # 紫色上的文字/图标（头像、发送键）

    BG: str = "#FAFAFC"  # 页面背景
    SURFACE: str = "#FFFFFF"  # 卡片、Header、Input Bar
    SURFACE_SECONDARY: str = "#F2F2F6"  # 输入框、禁用态、标签

    BORDER: str = "#E8E8EF"  # 描边、分隔线
    BORDER_STRONG: str = "#D8D8E3"  # 较强描边

    TEXT_PRIMARY: str = "#1F1F2E"  # 正文、标题
    TEXT_SECONDARY: str = "#6B6B7B"  # 称号、状态、提示
    TEXT_MUTED: str = "#9CA3AF"  # 占位符、空态

    SUCCESS: str = "#22C55E"  # 在线状态点
    ERROR: str = "#EF4444"  # 错误提示
    ERROR_SURFACE: str = "#FEF2F2"  # 错误提示背景
    SHADOW: str = "rgba(31, 31, 46, 0.12)"  # 悬浮按钮等轻微投影


@dataclass(frozen=True)
class Typography:
    """字号与字重 Token。"""

    DISPLAY: int = 24
    HEADLINE: int = 18
    TITLE: int = 16
    BODY: int = 14
    CAPTION: int = 12
    TINY: int = 11

    WEIGHT_NORMAL: str = "normal"
    WEIGHT_BOLD: str = "bold"
    WEIGHT_500: str = "w500"


@dataclass(frozen=True)
class Spacing:
    """间距 Token（4/8/12/16/24 网格）。"""

    XS: int = 4
    SM: int = 8
    MD: int = 12
    LG: int = 16
    XL: int = 24
    XXL: int = 32


@dataclass(frozen=True)
class Radius:
    """圆角 Token。"""

    SM: int = 8
    MD: int = 12
    LG: int = 16
    FULL: int = 9999


@dataclass(frozen=True)
class Layout:
    """布局常量。"""

    HEADER_HEIGHT: int = 64
    INPUT_BAR_MIN_HEIGHT: int = 72
    WINDOW_WIDTH: int = 520
    WINDOW_HEIGHT: int = 800
    WINDOW_MIN_WIDTH: int = 400
    WINDOW_MIN_HEIGHT: int = 600
    DRAWER_WIDTH: int = 320
    DRAWER_MAX_WIDTH_RATIO: float = 0.7
    USER_BUBBLE_MAX_RATIO: float = 0.75
    AI_BUBBLE_MAX_RATIO: float = 0.78
    AI_AVATAR_TEXT: str = "阿"
    USER_AVATAR_TEXT: str = "博"

    # 交互常量（避免在组件中散落魔法数）
    INPUT_MAX_LENGTH: int = 2000
    SCROLL_DURATION: int = 200
    SCROLL_DURATION_FAST: int = 100
    BUBBLE_TAIL: int = 4  # 气泡指向说话者的小圆角
    HEADER_PAD_Y: int = 10  # 顶栏上下内边距


@dataclass(frozen=True)
class Animations:
    """动效时长与曲线 Token。"""

    FAST: int = 150
    NORMAL: int = 250
    SLOW: int = 300
    EASE_OUT: str = "easeOut"
    EASE_IN_OUT: str = "easeInOut"


# 便捷导入别名
c = Colors()
t = Typography()
sp = Spacing()
r = Radius()
layout = Layout()
anim = Animations()
