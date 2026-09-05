"""聊天区组件：消息列表、空态、错误提示、滚动控制。"""
from __future__ import annotations

from typing import Callable, List, Optional

import flet as ft

from ui.theme import ALIGN_CENTER, c, t, sp, r, layout, anim
from ui.components.chat_bubble import ChatBubble
from ui.components.date_nav import format_day
from ui.components.speaker import MuteState
from ui.components.thinking_overlay import ThinkingOverlay
from ui.components.tts_status import TTSStatusState
from ui.components.empty_state import EmptyState
from ui.design.avatar_provider import AvatarProvider


class ChatArea(ft.Container):
    """主聊天区，承载消息气泡与状态。"""

    def __init__(
        self,
        persona,
        avatar_provider: AvatarProvider | None = None,
        on_speak: Callable[[str], None] | None = None,
        mute_state: Optional["MuteState"] = None,
        tts_state: Optional["TTSStatusState"] = None,
    ) -> None:
        super().__init__()
        self.persona = persona
        self._avatar_provider = avatar_provider
        self._on_speak = on_speak
        self._mute_state = mute_state
        self._tts_state = tts_state
        self._has_messages = False
        self._auto_scroll = True
        self._thinking_indicator: ft.Control | None = None
        self._empty_state = EmptyState(persona, avatar_provider=avatar_provider)
        # 聊天区文本可用宽度（由 app 层按窗口/分栏计算后注入；0 = 未知，不限宽）
        self._chat_text_width: float = 0

        self._scroll_btn = self._build_scroll_btn()
        self._list = ft.ListView(
            controls=[self._empty_state],
            spacing=sp.SM,
            padding=ft.Padding.only(left=sp.LG, right=sp.LG, top=sp.LG, bottom=sp.SM),
            auto_scroll=True,
            expand=True,
            on_scroll=self._on_scroll,
        )
        self.content = ft.Stack([self._list, self._scroll_btn], expand=True)
        # 透明背景：让皮肤壁纸透出（气泡本身是 SURFACE/PRIMARY_LIGHT 不透明卡片，
        # 聊天文字始终落在卡片上，可读性不受影响）
        self.bgcolor = None
        self.expand = True

    def _build_scroll_btn(self) -> ft.Container:
        return ft.Container(
            content=ft.Icon(ft.Icons.ARROW_DOWNWARD_ROUNDED, color=c.PRIMARY, size=20),
            width=40,
            height=40,
            bgcolor=c.SURFACE,
            border=ft.Border.all(width=1, color=c.BORDER),
            border_radius=r.FULL,
            alignment=ALIGN_CENTER,
            right=sp.LG,
            bottom=sp.LG + sp.SM,
            visible=False,
            tooltip="回到最新消息",
            on_click=self._scroll_to_bottom,
            shadow=ft.BoxShadow(blur_radius=6, color=c.SHADOW),
        )

    def _append_fade(self, ctrl: ft.Control) -> None:
        """追加控件并播放淡入动画。"""
        ctrl.opacity = 0
        ctrl.animate_opacity = ft.Animation(anim.NORMAL, anim.EASE_OUT)
        self._list.controls.append(ctrl)
        self._list.update()
        ctrl.opacity = 1
        ctrl.update()

    @staticmethod
    def _safe_page(control: ft.Control):
        """安全获取控件所属 page；未挂载时返回 None（避免触发 RuntimeError）。"""
        try:
            return control.page
        except RuntimeError:
            return None

    def _scroll_if_auto(self, duration: int = layout.SCROLL_DURATION) -> None:
        page = self._safe_page(self._list)
        if self._auto_scroll and page is not None:
            # Flet 0.86 的 scroll_to 为协程，须经 run_task 调度
            page.run_task(self._async_scroll, duration)

    async def _async_scroll(self, duration: int) -> None:
        await self._list.scroll_to(offset=-1, duration=duration)

    def set_chat_text_width(self, width: float) -> None:
        """更新聊天区文本可用宽度（拖手柄 / 窗口 resize 时由 app 层调用）。

        只回写气泡的**外层容器宽度**，不重建气泡——重建会打断流式输出，
        并且要重新挂 🔊 的静音/忙碌订阅，风险与收益不成比例。
        """
        try:
            width = max(0.0, float(width or 0))
        except (TypeError, ValueError):
            return
        if width == self._chat_text_width:
            return
        self._chat_text_width = width
        for ctrl in self._list.controls:
            ChatBubble.apply_width(ctrl, width)
        try:
            self._list.update()
        except Exception:  # noqa: BLE001 - 未挂载时忽略
            pass

    def _ensure_message_list(self) -> None:
        """第一条消息加入时移除空态。"""
        if not self._has_messages:
            self._has_messages = True
            self._list.controls.clear()

    def add_user(self, text: str) -> None:
        """添加用户消息并显示思考态覆盖层。"""
        self._ensure_message_list()
        self._append_fade(ChatBubble.user(text, available_width=self._chat_text_width))
        self._thinking_indicator = ThinkingOverlay(self._avatar_provider)
        self._list.controls.append(self._thinking_indicator)
        self._list.update()
        self._scroll_if_auto()

    def set_phase(self, phase: str) -> None:
        """由 Agent 阶段事件驱动思考态检索进度（RAG 可视化）。"""
        if isinstance(self._thinking_indicator, ThinkingOverlay):
            self._thinking_indicator.set_phase(phase)

    def start_assistant(self) -> ft.Text:
        """开始助手回复，移除思考指示器并返回文本控件用于流式更新。"""
        self._remove_thinking()
        text_control = ft.Text("", color=c.TEXT_PRIMARY, size=14, selectable=True)
        bubble = ChatBubble.assistant(
            "",
            text_control=text_control,
            avatar_provider=self._avatar_provider,
            on_speak=self._on_speak,
            mute_state=self._mute_state,
            tts_state=self._tts_state,
            available_width=self._chat_text_width,
        )
        self._append_fade(bubble)
        self._scroll_if_auto(layout.SCROLL_DURATION_FAST)
        return text_control

    def append_assistant_text(self, text_control: ft.Text, delta: str) -> None:
        """向助手文本控件追加流式内容。"""
        text_control.value += delta
        text_control.update()
        self._scroll_if_auto(layout.SCROLL_DURATION_FAST)

    def remove_thinking(self) -> None:
        """移除思考指示器（回复为空或失败时）。"""
        self._remove_thinking()

    def _remove_thinking(self) -> None:
        if self._thinking_indicator is not None and self._thinking_indicator in self._list.controls:
            self._list.controls.remove(self._thinking_indicator)
            self._thinking_indicator = None
            self._list.update()

    def add_assistant(self, text: str) -> None:
        """直接添加完整的助手消息（非流式或兜底）。"""
        self._ensure_message_list()
        self._remove_thinking()
        self._append_fade(
            ChatBubble.assistant(
                text,
                avatar_provider=self._avatar_provider,
                on_speak=self._on_speak,
                mute_state=self._mute_state,
                tts_state=self._tts_state,
                available_width=self._chat_text_width,
            )
        )
        self._scroll_if_auto()

    # ----- 按日期回放（时间轴 UI） -----
    def show_day(self, day: str, messages: list, today: str) -> None:
        """用某一天的库内对话重建聊天区。

        - day == today：交互模式（正常欢迎空态 / 历史回放），可继续输入
        - day != today：只读浏览——顶部加只读横幅，输入框由 app 层隐藏
        历史气泡的 🔊 朗读仍可用（朗读的是历史台词本身，无副作用）。
        """
        self._unsubscribe_mute(self._list.controls)
        self._thinking_indicator = None
        self._has_messages = bool(messages)
        self._list.controls.clear()
        if day != today:
            self._list.controls.append(self._make_readonly_banner(day))
        if not messages:
            # 今天没聊过 → 正常欢迎空态；别的天没记录 → 空日提示
            self._list.controls.append(
                self._empty_state if day == today else self._make_empty_day_hint(day)
            )
        else:
            for m in messages:
                role = m.get("role")
                content = m.get("content") or ""
                if not content:
                    continue
                if role == "user":
                    self._list.controls.append(
                        ChatBubble.user(content, available_width=self._chat_text_width)
                    )
                elif role == "assistant":
                    self._list.controls.append(
                        ChatBubble.assistant(
                            content,
                            avatar_provider=self._avatar_provider,
                            on_speak=self._on_speak,
                            mute_state=self._mute_state,
                            tts_state=self._tts_state,
                            available_width=self._chat_text_width,
                        )
                    )
        self._list.update()
        self._scroll_if_auto()

    def _make_readonly_banner(self, day: str) -> ft.Control:
        return ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.HISTORY_ROUNDED, size=14, color=c.TEXT_MUTED),
                    ft.Text(
                        f"正在查看 {format_day(day)} 的对话（只读）",
                        color=c.TEXT_MUTED,
                        size=t.CAPTION,
                    ),
                ],
                spacing=sp.SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            alignment=ALIGN_CENTER,
            margin=ft.Margin.only(top=sp.SM, bottom=0),
        )

    def _make_empty_day_hint(self, day: str) -> ft.Control:
        return ft.Container(
            content=ft.Text(
                f"{format_day(day)} 没有对话记录",
                color=c.TEXT_MUTED,
                size=t.CAPTION,
                text_align=ft.TextAlign.CENTER,
            ),
            alignment=ALIGN_CENTER,
            expand=True,
            padding=sp.XL,
        )

    def add_error(self, text: str) -> None:
        """添加系统错误提示。"""
        self._ensure_message_list()
        self._remove_thinking()
        self._append_fade(ChatBubble.error(text, available_width=self._chat_text_width))
        self._scroll_if_auto()

    def clear(self) -> None:
        """清空聊天区，回到空态。气泡销毁前先退订其静音回调，防订阅泄漏。"""
        self._unsubscribe_mute(self._list.controls)
        self._has_messages = False
        self._thinking_indicator = None
        self._list.controls.clear()
        self._list.controls.append(self._empty_state)
        self._list.update()

    @staticmethod
    def _unsubscribe_mute(controls) -> None:
        """对列表中的气泡退订 MuteState / TTSStatusState（订阅句柄挂在控件上）。"""
        for ctrl in controls:
            for state_attr, cb_attr in (("_mute_state", "_mute_cb"), ("_tts_state", "_tts_cb")):
                state = getattr(ctrl, state_attr, None)
                cb = getattr(ctrl, cb_attr, None)
                if state is not None and cb is not None:
                    state.unsubscribe(cb)

    def _on_scroll(self, e: ft.OnScrollEvent) -> None:
        if e.max_scroll_extent is None:
            return
        at_bottom = e.pixels >= e.max_scroll_extent - 8
        if at_bottom and not self._auto_scroll:
            self._auto_scroll = True
            self._list.auto_scroll = True
            self._scroll_btn.visible = False
            self._scroll_btn.update()
        elif not at_bottom and self._auto_scroll:
            self._auto_scroll = False
            self._list.auto_scroll = False
            self._scroll_btn.visible = True
            self._scroll_btn.update()

    def _scroll_to_bottom(self, _e: ft.ControlEvent) -> None:
        self._auto_scroll = True
        self._list.auto_scroll = True
        self._scroll_btn.visible = False
        page = self._safe_page(self._list)
        if page is not None:
            page.run_task(self._async_scroll, layout.SCROLL_DURATION)
        self._scroll_btn.update()
