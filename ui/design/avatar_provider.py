"""头像提供方抽象：解耦头像资源，便于未来按情绪切换图像头像。

v2 默认使用 TextAvatarProvider（文字「阿」），零素材依赖、无版权风险。
后续放入 resources/avatar/*.png 并用 ImageAvatarProvider 即可启用图像头像，
UI 代码无需改动——这是 Emotion 模块落地后"头像随情绪变化"的基础设施。
"""
from __future__ import annotations

import os

import flet as ft

from ui.theme import c


class AvatarProvider:
    """头像提供方基类。UI 经 get() 取头像，不关心底层是文字还是图片。"""

    def get(
        self,
        *,
        state_key: str = "calm",
        radius: int,
        text_size: int | None = None,
        **kwargs,
    ) -> ft.Control:
        raise NotImplementedError


class TextAvatarProvider(AvatarProvider):
    """文字头像提供方（v2 默认）。"""

    def __init__(
        self,
        text: str = "阿",
        bgcolor=c.PRIMARY,
        text_color=c.ON_PRIMARY,
    ) -> None:
        self._text = text
        self._bgcolor = bgcolor
        self._text_color = text_color

    def get(
        self,
        *,
        state_key: str = "calm",
        radius: int,
        text_size: int | None = None,
        bgcolor=None,
        text_color=None,
        **kwargs,
    ) -> ft.Control:
        return ft.CircleAvatar(
            bgcolor=bgcolor or self._bgcolor,
            radius=radius,
            content=ft.Text(
                self._text,
                color=text_color or self._text_color,
                size=text_size if text_size is not None else max(12, radius),
                weight=ft.FontWeight.BOLD,
            ),
        )


class ImageAvatarProvider(AvatarProvider):
    """图像头像提供方（未来启用）：按情绪状态键读取 resources/avatar/<state>.png。

    若 <state>.png 缺失，会尝试同目录下的 default.png，仍缺失才回退到 fallback
    （默认文字头像），保证可用性并避免未来多情绪头像时的闪烁。
    """

    def __init__(
        self,
        folder: str,
        fallback: AvatarProvider | None = None,
        size: int = 96,
    ) -> None:
        self._folder = folder
        self._fallback = fallback or TextAvatarProvider()
        self._size = size

    def get(
        self,
        *,
        state_key: str = "calm",
        radius: int,
        text_size: int | None = None,
        **kwargs,
    ) -> ft.Control:
        path = os.path.join(self._folder, f"{state_key}.png")
        if not os.path.isfile(path):
            path = os.path.join(self._folder, "default.png")
        if os.path.isfile(path):
            return ft.CircleAvatar(
                radius=radius,
                content=ft.Image(
                    src=path,
                    width=2 * radius,
                    height=2 * radius,
                    border_radius=radius,
                ),
            )
        return self._fallback.get(state_key=state_key, radius=radius, text_size=text_size, **kwargs)


class SkinAvatarProvider(AvatarProvider):
    """皮肤头像提供方：同皮肤内统一头像，不随情绪状态键切换图片。

    与 ImageAvatarProvider 的区别：皮肤系统里"一张图 = 头像 + 壁纸"，
    头像固定为当前皮肤的 avatar.png，情绪键不再换图（避免同皮肤内闪烁）。
    缺失时回退到 default_path（resources/avatar/default.png），再缺才回退
    fallback（默认文字头像）。

    只接收路径而非 Skin 对象，避免与 ui/design/skin.py 形成循环导入。
    """

    def __init__(
        self,
        avatar_path: str,
        default_path: str | None = None,
        fallback: AvatarProvider | None = None,
        size: int = 96,
    ) -> None:
        self._avatar_path = avatar_path
        self._default_path = default_path
        self._fallback = fallback or TextAvatarProvider()
        self._size = size

    def get(
        self,
        *,
        state_key: str = "calm",
        radius: int,
        text_size: int | None = None,
        **kwargs,
    ) -> ft.Control:
        path = self._avatar_path
        if not os.path.isfile(path) and self._default_path:
            path = self._default_path
        if os.path.isfile(path):
            return ft.CircleAvatar(
                radius=radius,
                content=ft.Image(
                    src=path,
                    width=2 * radius,
                    height=2 * radius,
                    border_radius=radius,
                ),
            )
        return self._fallback.get(state_key=state_key, radius=radius, text_size=text_size, **kwargs)
