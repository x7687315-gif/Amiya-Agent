"""可复用头像构造助手，经 AvatarProvider 取头像。

UI 不直接构造头像，全部走 make_avatar(provider, ...) —— 未来 Emotion 模块切换
provider.get(state) 即可让助手头像随情绪变化，无需改调用方。
"""
from __future__ import annotations

import flet as ft

from ui.theme import c
from ui.design.avatar_provider import AvatarProvider, TextAvatarProvider

_DEFAULT_PROVIDER = TextAvatarProvider(text="阿", bgcolor=c.PRIMARY, text_color=c.ON_PRIMARY)


def make_avatar(
    provider: AvatarProvider | None = None,
    *,
    state_key: str = "calm",
    radius: int = 20,
    text_size: int | None = None,
    **kwargs,
) -> ft.Control:
    """生成助手头像（经 provider）。

    Args:
        provider: 头像提供方；缺省用默认文字头像。
        state_key: 情绪状态键（calm/thinking/worried/happy），供未来图像头像切换。
        radius: 头像半径。
        text_size: 文字大小；缺省按半径估算。
        kwargs: 透传给 provider.get（如 bgcolor / text_color 覆盖）。
    """
    provider = provider or _DEFAULT_PROVIDER
    return provider.get(state_key=state_key, radius=radius, text_size=text_size, **kwargs)


def make_user_avatar(radius: int = 20, text_size: int | None = None) -> ft.Control:
    """用户头像（固定文字「博」，浅底深字）。"""
    return ft.CircleAvatar(
        bgcolor=c.SURFACE_SECONDARY,
        radius=radius,
        content=ft.Text(
            "博",
            color=c.TEXT_SECONDARY,
            size=text_size if text_size is not None else max(12, radius),
            weight=ft.FontWeight.BOLD,
        ),
    )
