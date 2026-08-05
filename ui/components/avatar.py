"""可复用头像构造助手，消除各组件中重复的 CircleAvatar 代码。"""
from __future__ import annotations

import flet as ft

from ui.theme import c


def make_avatar(
    text: str,
    radius: int,
    bgcolor,
    text_color=None,
    text_size: int | None = None,
) -> ft.CircleAvatar:
    """生成圆形文字头像（助手用「阿」，用户用「博」）。

    Args:
        text: 头像内文字。
        radius: 头像半径。
        bgcolor: 背景色（通常为紫色系 Token）。
        text_color: 文字色，默认 c.ON_PRIMARY（白）。
        text_size: 文字大小；缺省时按半径估算。
    """
    return ft.CircleAvatar(
        bgcolor=bgcolor,
        radius=radius,
        content=ft.Text(
            text,
            color=text_color or c.ON_PRIMARY,
            size=text_size if text_size is not None else max(12, radius),
            weight=ft.FontWeight.BOLD,
        ),
    )
