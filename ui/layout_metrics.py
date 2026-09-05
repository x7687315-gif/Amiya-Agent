"""布局度量：把「窗口 / 分栏 / 气泡」的宽度关系收敛为纯函数。

背景（docs/20_UI_ADAPTATION_ISSUES.md 的 P1 / P2 / P5 / P7）：
这些数值原先散落在两处且互不联动——

- ``ui/app.py``：``LEFT_MIN/LEFT_MAX = 180/560``、``RIGHT_MIN/RIGHT_MAX = 220/620``
  是**静态常量**，两边拉满（560+620=1180）恰好等于默认窗口宽，聊天区直接归零；
- ``ui/components/chat_bubble.py``：``max_width_ratio`` 是**死参数**（声明了从未
  使用），气泡靠 ``expand=bool(on_speak)`` 无条件撑满整行；两侧 64px margin 是
  「聊天区 ≥700px」时代的固定值。

结果是：窗口一窄或分栏一拖，四处同时失配。本模块把它们收敛成**纯函数**
（刻意不 import flet），目的有二：

1. **单一真源**：app（钳制）与 bubble（上限/边距）共用同一套计算，不再各写各的；
2. **可测**：不依赖 Flet 运行时即可写回归测试（见 tests/test_ui_layout_metrics.py）。
"""
from __future__ import annotations

# —— 结构常量（必须与控件实现保持一致）——
HANDLE_WIDTH = 9  # SplitHandle 竖条宽（ui/components/split_handle.py）
HANDLE_COUNT = 2  # 左栏|聊天区、聊天区|右栏 各一个手柄
CHAT_LIST_PADDING = 32  # ChatArea 的 ListView 左右 padding 各 sp.LG(16)

# —— 保底 / 下限 ——
CHAT_MIN_WIDTH = 420  # 聊天区（中间栏）最小可用宽度，含 list padding
LEFT_MIN = 220  # P5：身份行「头像 52 + 间距 + 名字」的最小舒适宽（原 180）
RIGHT_MIN = 220
LEFT_PREF_MAX = 560  # 静态偏好上限；实际还要再按窗口宽度动态收窄
RIGHT_PREF_MAX = 620

# —— 气泡 ——
BUBBLE_SIDE_MARGIN_MAX = 64  # 宽聊天区下的呼吸边距上限（原固定 64）
BUBBLE_SIDE_MARGIN_RATIO = 0.08  # 窄幅时按比例收缩（P7）


def chat_column_width(
    window_width: float,
    left_width: float,
    right_width: float,
    handle_width: int = HANDLE_WIDTH,
    handle_count: int = HANDLE_COUNT,
) -> int:
    """中间栏（聊天区 Column）宽度。"""
    return int(window_width - left_width - right_width - handle_width * handle_count)


def chat_text_width(
    window_width: float,
    left_width: float,
    right_width: float,
    handle_width: int = HANDLE_WIDTH,
    handle_count: int = HANDLE_COUNT,
    list_padding: int = CHAT_LIST_PADDING,
) -> int:
    """聊天区内文本可用宽度（再扣掉 ListView 左右 padding）。

    气泡的 max_width 与 side_margin 都以这个值（而非窗口宽）为基准，
    因为气泡实际是画在 ListView 的 padding 之内的。
    """
    col = chat_column_width(window_width, left_width, right_width, handle_width, handle_count)
    return max(0, col - list_padding)


def bubble_side_margin(chat_width: float) -> int:
    """P7：气泡让位边距 = min(64, 聊天区宽 × 8%)。

    宽聊天区保持 64px 的呼吸感，窄幅自动收窄，避免吃掉本就不多的文本带宽。
    """
    if chat_width <= 0:
        return BUBBLE_SIDE_MARGIN_MAX  # 未知宽度：回退到原固定值
    return max(0, int(min(BUBBLE_SIDE_MARGIN_MAX, chat_width * BUBBLE_SIDE_MARGIN_RATIO)))


def bubble_max_width(chat_width: float, ratio: float, reserved: float = 0) -> int:
    """P1：气泡内容宽度上限。

    - 理想上限 = ``ratio × chat_width``（气泡最宽占聊天区的 75%/78%）；
    - 但气泡行里还站着头像/朗读键/间距（``reserved``），窄幅下 ``ratio × 宽``
      可能比"剩余空间"还大导致行溢出，故最终取
      ``min(ratio × chat_width, chat_width − reserved)``。
      宽幅下前者生效（保持设计比例），窄幅下后者生效（保证不溢出）。

    返回 0 表示「不设上限」（调用方据此不写 width），用于宽度未知时
    回退到内容自适应，保持向后兼容。
    """
    if chat_width <= 0 or ratio <= 0:
        return 0
    by_ratio = int(chat_width * ratio)
    if reserved > 0:
        by_ratio = min(by_ratio, max(0, int(chat_width - reserved)))
    return max(0, by_ratio)


def clamp_left_width(
    desired: float,
    right_width: float,
    window_width: float,
    handle_width: int = HANDLE_WIDTH,
    handle_count: int = HANDLE_COUNT,
    chat_min: int = CHAT_MIN_WIDTH,
    left_min: int = LEFT_MIN,
    left_pref_max: int = LEFT_PREF_MAX,
) -> int:
    """P2：左栏宽度钳制，上限由窗口宽度动态推导。

    上限 = 窗口宽 − 右栏 − 手柄 − 聊天区保底，从而保证「左栏拖到底也压不死
    聊天区」。下限仍为 left_min：窗口过窄时宁可让聊天区略小于保底，也不让
    左栏塌缩到不可用（min 窗口 960 下两者可同时满足，见测试）。
    """
    dynamic_max = window_width - right_width - handle_width * handle_count - chat_min
    upper = max(left_min, min(left_pref_max, dynamic_max))
    return int(max(left_min, min(desired, upper)))


def clamp_right_width(
    desired: float,
    left_width: float,
    window_width: float,
    handle_width: int = HANDLE_WIDTH,
    handle_count: int = HANDLE_COUNT,
    chat_min: int = CHAT_MIN_WIDTH,
    right_min: int = RIGHT_MIN,
    right_pref_max: int = RIGHT_PREF_MAX,
) -> int:
    """P2：右栏宽度钳制（与 clamp_left_width 对称）。"""
    dynamic_max = window_width - left_width - handle_width * handle_count - chat_min
    upper = max(right_min, min(right_pref_max, dynamic_max))
    return int(max(right_min, min(desired, upper)))


def clamp_pair(
    left_width: float,
    right_width: float,
    window_width: float,
    **kwargs,
) -> tuple[int, int]:
    """同时钳制两侧，返回 (left, right)。顺序敏感：**先钳左、再钳右**。

    用于**校验持久化值**：换显示器 / 窗口变小后，上次保存的布局可能已非法
    （两侧之和超出当前窗口）。
    必须先钳左的原因：左栏被抬到地板值（LEFT_MIN）会吃掉聊天区宽度，若此时
    仍按**旧左栏**钳右，右栏会保留一个过大的值，聊天区最终跌破保底。先左后右
    保证右栏在「新左栏」的约束下收敛，聊天区恒 ≥ CHAT_MIN_WIDTH（见测试）。
    """
    left = clamp_left_width(left_width, right_width, window_width, **kwargs)
    right = clamp_right_width(right_width, left, window_width, **kwargs)
    return left, right


def window_width_of(page) -> int:
    """从 page 取当前窗口宽度；取不到时返回 0（调用方自行回退默认值）。

    容错原因：page.window.width 在某些平台/时机可能为 None（例如窗口尚未
    完成首次布局），此时不能让布局计算崩溃或产出负值。
    """
    try:
        width = getattr(getattr(page, "window", None), "width", None)
    except Exception:  # noqa: BLE001 - page 可能未就绪
        return 0
    if width is None:
        return 0
    try:
        return int(width)
    except (TypeError, ValueError):
        return 0


def window_height_of(page) -> int:
    """从 page 取当前窗口高度；取不到时返回 0（调用方自行回退默认值）。"""
    try:
        height = getattr(getattr(page, "window", None), "height", None)
    except Exception:  # noqa: BLE001 - page 可能未就绪
        return 0
    if height is None:
        return 0
    try:
        return int(height)
    except (TypeError, ValueError):
        return 0
