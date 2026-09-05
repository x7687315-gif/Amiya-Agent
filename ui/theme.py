"""Design Token：assistant-agent 视觉系统。

所有 UI 组件必须从此模块导入样式常量，禁止硬编码色值/字号/间距。
风格：浅色背景 + 几何极简主义 + 细描边 + 少阴影 + 本地（舰桥）基调。
"""
from __future__ import annotations

from dataclasses import dataclass, fields

import flet as ft


# 便捷对齐常量（Flet 0.86 不再提供 ft.alignment.center 等预设）
ALIGN_CENTER = ft.alignment.Alignment(0, 0)
ALIGN_CENTER_RIGHT = ft.alignment.Alignment(1, 0)
ALIGN_CENTER_LEFT = ft.alignment.Alignment(-1, 0)  # 壁纸左锚用（docs/20 P4）
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
    SURFACE_GLASS: str = "#C8F5F7FC"  # 半透明白玻璃（输入框等需要透出壁纸的浅底，78% 白）

    BORDER: str = "#E8E8EF"  # 描边、分隔线
    BORDER_STRONG: str = "#D8D8E3"  # 较强描边

    TEXT_PRIMARY: str = "#1F1F2E"  # 正文、标题
    TEXT_SECONDARY: str = "#6B6B7B"  # 称号、状态、提示
    TEXT_MUTED: str = "#9CA3AF"  # 占位符、空态

    SUCCESS: str = "#22C55E"  # 在线状态点
    ERROR: str = "#EF4444"  # 错误提示
    ERROR_SURFACE: str = "#FEF2F2"  # 错误提示背景
    SHADOW: str = "rgba(31, 31, 46, 0.12)"  # 悬浮按钮等轻微投影

    # —— 情绪状态色（左栏状态 + 头像切换）——
    STATE_CALM: str = "#8FBF9F"  # 平静·绿
    STATE_THINKING: str = "#7C6BC4"  # 思考·紫（复用主色）
    STATE_WORRIED: str = "#E0A458"  # 担忧·琥珀
    STATE_HAPPY: str = "#E58FA8"  # 愉悦·粉

    # —— 信赖度（进度条两端）——
    TRUST_LOW: str = "#C9C3E0"
    TRUST_HIGH: str = "#7C6BC4"

    # —— 舰桥强调（极淡青，仅分隔线/光晕，克制使用）——
    BRIDGE_ACCENT: str = "#7FB8C4"


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
    WINDOW_WIDTH: int = 1180  # v2 三栏默认宽度
    WINDOW_HEIGHT: int = 760
    WINDOW_MIN_WIDTH: int = 960  # 固定最小宽度，窄屏不降级
    WINDOW_MIN_HEIGHT: int = 640
    DRAWER_WIDTH: int = 320
    DRAWER_MAX_WIDTH_RATIO: float = 0.7
    USER_BUBBLE_MAX_RATIO: float = 0.75
    AI_BUBBLE_MAX_RATIO: float = 0.78
    AI_AVATAR_TEXT: str = "阿"
    USER_AVATAR_TEXT: str = "博"
    LEFT_COL_WIDTH: int = 260  # 左栏固定宽
    RIGHT_COL_WIDTH: int = 300  # 右栏固定宽
    COLUMN_GAP: int = 0  # 栏间用 1px 描边分隔，不做间距

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

    STAGGER: int = 80  # 三栏入场依次延迟
    PULSE_MS: int = 1200  # 状态点脉冲周期（文档约定；脉冲经 animate_opacity 实现）
    BREATH_MS: int = 8000  # 背景光晕呼吸周期


@dataclass(frozen=True)
class EmotionState:
    """情绪状态（供左栏状态与头像切换）。"""

    key: str
    emoji: str
    label: str
    color: str


# 便捷导入别名
c = Colors()
t = Typography()
sp = Spacing()
r = Radius()
layout = Layout()
anim = Animations()

# 情绪状态映射（须在 c 定义后，因引用 c.STATE_*）
EMOTIONS: dict[str, EmotionState] = {
    "calm": EmotionState("calm", "🌱", "平静", c.STATE_CALM),
    "thinking": EmotionState("thinking", "🤔", "思考", c.STATE_THINKING),
    "worried": EmotionState("worried", "😟", "担忧", c.STATE_WORRIED),
    "happy": EmotionState("happy", "🙂", "愉悦", c.STATE_HAPPY),
}

DEFAULT_EMOTION = "calm"


def apply_skin(colors: Colors) -> None:
    """把皮肤色板**一次性**写入单例 ``c``（必须在构造任何 UI 之前调用）。

    设计说明（皮肤系统计划 §4「无 DynamicColors」）：
    - 这不是动态代理。``c`` 仍是一个普通的 ``Colors`` 单例，只是在启动阶段被
      初始化一次。所有组件继续 ``from ui.theme import c``，调用点零改动。
    - 因为各组件是直接 ``import c``（绑定的是同一个对象引用），所以这里必须
      **原地修改** ``c`` 的字段，而不能把 ``c`` 重新绑定到新对象——否则已导入方
      仍指向旧实例。``Colors`` 是 frozen dataclass，故用 ``object.__setattr__``
      绕过冻结做这一次性初始化（每个会话只应在启动时调用一次）。
    - 运行时完整换肤（若将来真做）再引入动态 ThemeProvider，不在此处预支。
    """
    for f in fields(Colors):
        object.__setattr__(c, f.name, getattr(colors, f.name))
    # EMOTIONS 为可变 dict，原地替换条目，使 STATE_* 颜色与新色板保持同步。
    for key, state in EMOTIONS.items():
        EMOTIONS[key] = EmotionState(
            state.key, state.emoji, state.label, getattr(c, f"STATE_{key.upper()}")
        )
