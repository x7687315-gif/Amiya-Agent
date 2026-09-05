"""聊天气泡组件。"""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from ui.theme import (
    ALIGN_CENTER_LEFT,
    ALIGN_CENTER_RIGHT,
    c,
    t,
    sp,
    r,
    layout,
)
from ui.components.avatar import make_avatar
from ui.components.speaker import MuteState
from ui.components.tts_status import TTSStatusState
from ui.design.avatar_provider import AvatarProvider
from ui.layout_metrics import bubble_max_width, bubble_side_margin


class ChatBubble:
    """气泡工厂，返回 Flet 控件。

    宽度契约（docs/20_UI_ADAPTATION_ISSUES.md 的 P1 / P7）：
    - ``available_width`` 为**聊天区文本可用宽度**（已扣除 ListView padding）。
      传入后气泡内容宽度被限制在 ``ratio × available_width``，短消息仍按内容
      自适应（用带 alignment 的容器做「至多」语义，而非写死宽度把短气泡拉长）。
    - 侧边让位边距由固定 64px 改为 ``min(64, 8% × available_width)``，窄聊天区
      自动收窄。
    - ``available_width=0`` 表示宽度未知，回退到内容自适应 + 原固定边距，
      保持向后兼容（测试与离线构造场景）。
    """

    @staticmethod
    def user(
        text: str,
        max_width_ratio: float = layout.USER_BUBBLE_MAX_RATIO,
        available_width: float = 0,
    ) -> ft.Control:
        """用户气泡（右侧，淡紫底）。"""
        side = bubble_side_margin(available_width)
        # reserved = cap 自身左右 margin（左侧弹性 spacer 不占固定宽）
        max_w = bubble_max_width(available_width, max_width_ratio, reserved=side + sp.SM)

        bubble = ft.Container(
            content=ft.Text(
                text,
                color=c.TEXT_PRIMARY,
                size=t.BODY,
                selectable=True,
            ),
            bgcolor=c.PRIMARY_LIGHT,
            padding=ft.Padding.only(
                left=sp.MD + 2, right=sp.MD + 2, top=sp.SM + 1, bottom=sp.SM + 1
            ),
            border_radius=ft.BorderRadius.only(
                top_left=r.LG,
                top_right=r.LG,
                bottom_left=layout.BUBBLE_TAIL,
                bottom_right=r.LG,
            ),
        )
        # cap 承担「宽度上限 + 让位边距」：设 alignment 后容器给子元素**松约束**，
        # 因而短气泡仍是内容宽（右对齐），长气泡才被 max_w 截断。
        cap_kwargs: dict = {}
        if max_w:
            cap_kwargs["width"] = max_w
            cap_kwargs["alignment"] = ALIGN_CENTER_RIGHT
        cap = ft.Container(
            content=bubble,
            margin=ft.Margin.only(left=side, right=sp.SM, top=4, bottom=4),
            **cap_kwargs,
        )
        holder = ft.Row([ft.Container(expand=True), cap], spacing=0)
        holder._bubble_cap = cap  # type: ignore[attr-defined]
        holder._bubble_kind = "user"  # type: ignore[attr-defined]
        holder._bubble_ratio = max_width_ratio  # type: ignore[attr-defined]
        return holder

    @staticmethod
    def assistant(
        text: str,
        text_control: ft.Text | None = None,
        avatar_provider: AvatarProvider | None = None,
        max_width_ratio: float = layout.AI_BUBBLE_MAX_RATIO,
        on_speak: Callable[[str], None] | None = None,
        mute_state: Optional["MuteState"] = None,
        tts_state: Optional["TTSStatusState"] = None,
        available_width: float = 0,
    ) -> ft.Control:
        """助手气泡（左侧，白底描边）。

        Args:
            text: 初始文本；若提供 text_control 则忽略。
            text_control: 可选的 ft.Text 控件，用于流式追加。
            on_speak: 可选朗读回调；提供则在气泡末尾追加 🔊 按钮，
                点击时朗读「本气泡自己的文本」（读 text_control.value，而非全局最新回复）。
            mute_state: 可选全局静音状态；提供则 🔊 按钮订阅它，静音时自动隐藏。
            tts_state: 可选语音忙碌状态；提供则 🔊 订阅它，合成/播放中禁用并转圈
                （顺带把连点并发合成收敛为串行）。
            available_width: 聊天区文本可用宽度（0 = 不限）。
        """
        content = text_control or ft.Text(
            text,
            color=c.TEXT_PRIMARY,
            size=t.BODY,
            selectable=True,
        )
        max_w = bubble_max_width(
            available_width,
            max_width_ratio,
            # 行内非气泡占位：头像 32 + 朗读键 40 + 行 spacing 3×8 + cap 左 margin 8
            reserved=104,
        )

        bubble = ft.Container(
            content=content,
            bgcolor=c.SURFACE,
            border=ft.Border.all(width=1, color=c.BORDER),
            padding=ft.Padding.only(
                left=sp.MD + 2, right=sp.MD + 2, top=sp.SM + 1, bottom=sp.SM + 1
            ),
            border_radius=ft.BorderRadius.only(
                top_left=layout.BUBBLE_TAIL,
                top_right=r.LG,
                bottom_left=r.LG,
                bottom_right=r.LG,
            ),
            # 右侧不再写死 64px：气泡宽度由 cap 限制在 ratio × 聊天区宽，
            # 剩余空间自然留白（P7）。
            margin=ft.Margin.only(left=sp.SM, top=4, bottom=4),
        )

        cap_kwargs: dict = {}
        if max_w:
            cap_kwargs["width"] = max_w
            cap_kwargs["alignment"] = ALIGN_CENTER_LEFT
        cap = ft.Container(content=bubble, **cap_kwargs)

        row: list[ft.Control] = [
            make_avatar(avatar_provider, state_key="calm", radius=16, text_size=13),
            cap,
        ]

        # M3-B：每个最终助手气泡末尾挂一个 🔊，点它只读这一句
        mute_cb = None
        tts_cb = None
        if on_speak is not None:
            speaker = ft.IconButton(
                icon=ft.Icons.VOLUME_UP,
                icon_color=c.PRIMARY,
                icon_size=16,
                tooltip="朗读这句",
                on_click=lambda _e: on_speak(content.value or ""),
            )

            def _apply_mute(muted: bool) -> None:
                # 订阅静音状态：静音时隐藏 🔊；无 page 时跳过 update 避免 RuntimeError
                speaker.visible = not muted
                try:
                    speaker.update()
                except Exception:  # noqa: BLE001 - 未挂载控件 update 会抛，忽略即可
                    pass

            def _apply_busy(busy: bool) -> None:
                # 合成/播放忙碌：禁用并转圈，防止连点并发合成
                speaker.disabled = busy
                speaker.icon = (
                    ft.Icons.HOURGLASS_TOP_ROUNDED if busy else ft.Icons.VOLUME_UP
                )
                speaker.tooltip = "语音生成/播放中…" if busy else "朗读这句"
                try:
                    speaker.update()
                except Exception:  # noqa: BLE001
                    pass

            if mute_state is not None:
                mute_state.subscribe(_apply_mute)
                mute_cb = _apply_mute
            else:
                speaker.visible = True
            if tts_state is not None:
                tts_state.subscribe(_apply_busy)
                tts_cb = _apply_busy
            # 用弹性占位把 🔊 推到行尾（替代原先「气泡 expand 撑满」的做法，
            # 否则气泡会无条件占满整行、宽度上限形同虚设）。
            row.append(ft.Container(expand=True))
            row.append(speaker)

        holder = ft.Row(
            row,
            spacing=sp.SM,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )
        # 供 ChatArea.clear() 退订，防止气泡销毁后订阅闭包滞留状态对象（泄漏）
        if mute_state is not None and mute_cb is not None:
            holder._mute_state = mute_state  # type: ignore[attr-defined]
            holder._mute_cb = mute_cb  # type: ignore[attr-defined]
        if tts_state is not None and tts_cb is not None:
            holder._tts_state = tts_state  # type: ignore[attr-defined]
            holder._tts_cb = tts_cb  # type: ignore[attr-defined]
        # 供布局变化时回写宽度上限
        holder._bubble_cap = cap  # type: ignore[attr-defined]
        holder._bubble_kind = "assistant"  # type: ignore[attr-defined]
        holder._bubble_ratio = max_width_ratio  # type: ignore[attr-defined]
        return holder

    @staticmethod
    def error(text: str, available_width: float = 0) -> ft.Control:
        """系统错误提示气泡（不伪装成助手台词）。"""
        side = bubble_side_margin(available_width)
        return ft.Row(
            [
                ft.Container(expand=True),
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=c.ERROR, size=16),
                            ft.Text(text, color=c.ERROR, size=t.CAPTION),
                        ],
                        spacing=sp.SM,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    bgcolor=c.ERROR_SURFACE,
                    border=ft.Border.all(width=1, color=c.ERROR),
                    border_radius=r.MD,
                    padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.SM, bottom=sp.SM),
                    margin=ft.Margin.only(left=side, right=side, top=sp.SM, bottom=sp.SM),
                ),
                ft.Container(expand=True),
            ],
            spacing=0,
        )

    @staticmethod
    def apply_width(holder: ft.Control, available_width: float) -> None:
        """回写一个已存在气泡的宽度上限/边距（拖手柄、窗口 resize 后调用）。

        只更新**容器**而不重建气泡，避免打断流式输出与 🔊 订阅状态。
        """
        cap = getattr(holder, "_bubble_cap", None)
        if cap is None:
            return
        ratio = getattr(holder, "_bubble_ratio", 0.0) or 0.0
        kind = getattr(holder, "_bubble_kind", "")
        max_w = bubble_max_width(available_width, ratio)
        if max_w:
            cap.width = max_w
        if kind == "user":
            side = bubble_side_margin(available_width)
            cap.margin = ft.Margin.only(left=side, right=sp.SM, top=4, bottom=4)
