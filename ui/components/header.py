"""顶部栏组件：头像、名字、称号、状态、人格抽屉入口。"""
from __future__ import annotations

from typing import Callable, Optional

import flet as ft

from ui.theme import c, t, sp, r, layout, anim
from ui.components.avatar import make_avatar
from ui.components.speaker import MuteState
from ui.design.avatar_provider import AvatarProvider


class Header(ft.Container):
    """顶部栏，展示助手身份与当前状态。"""

    def __init__(
        self,
        persona,
        on_persona_click: Callable[[], None] | None = None,
        avatar_provider: AvatarProvider | None = None,
        mute_state: Optional["MuteState"] = None,
        on_new_chat: Optional[Callable[[], None]] = None,
        voices: Optional[list] = None,
        current_voice: str = "",
        on_voice_change: Optional[Callable[[str], None]] = None,
        show_voice_controls: bool = True,
    ) -> None:
        super().__init__()
        self.persona = persona
        self.on_persona_click = on_persona_click
        self._avatar_provider = avatar_provider
        self._mute_state = mute_state
        self._on_new_chat = on_new_chat
        # P3 语音入口：声音切换（声音名经回调外传，组件不感知 TTSService/GPT-SoVITS）
        self._voices = list(voices or [])
        self._current_voice = current_voice
        self._on_voice_change = on_voice_change
        # 语音功能总开关（TTS_ENABLED=0 时隐藏静音键与声音菜单）
        self._show_voice_controls = show_voice_controls

        self._status_text = ft.Text("在线", color=c.TEXT_MUTED, size=t.CAPTION)
        self._status_dot = self._build_dot()
        self._status_row = ft.Row(
            [self._status_dot, self._status_text],
            spacing=sp.SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self._persona_btn = ft.IconButton(
            icon=ft.Icons.BADGE_OUTLINED,
            icon_color=c.TEXT_SECONDARY,
            tooltip="查看人格",
            on_click=lambda _e: self.on_persona_click() if self.on_persona_click else None,
        )
        self._new_chat_btn = ft.IconButton(
            icon=ft.Icons.ADD_COMMENT_ROUNDED,
            icon_color=c.TEXT_SECONDARY,
            tooltip="开启新对话（当天聊天仍可在日期里回看）",
            visible=on_new_chat is not None,
            on_click=lambda _e: self._on_new_chat() if self._on_new_chat else None,
        )
        self._mute_btn = self._build_mute_btn()
        self._voice_menu = self._build_voice_menu()
        self._build()

        # 入场淡入（由 app 在 page.add 后调用 reveal 触发）
        self.opacity = 0
        self.animate_opacity = ft.Animation(anim.NORMAL, anim.EASE_OUT)

    def _build_dot(self) -> ft.Container:
        return ft.Container(width=8, height=8, bgcolor=c.SUCCESS, border_radius=r.FULL)

    def _build_mute_btn(self) -> ft.IconButton:
        """M3-C 全局语音开关：🔊 开 / 🔇 静音。初始图标对齐当前状态。"""
        muted = self._mute_state.muted if self._mute_state is not None else False
        return ft.IconButton(
            icon=ft.Icons.VOLUME_OFF if muted else ft.Icons.VOLUME_UP,
            icon_color=c.TEXT_SECONDARY,
            tooltip="语音：开" if not muted else "语音：静音",
            on_click=self._on_mute_click,
            visible=self._show_voice_controls,
        )

    # ----- P3 声音切换入口 -----
    def _voice_items(self) -> list:
        return [
            ft.PopupMenuItem(
                content=ft.Text(v),
                data=v,
                checked=(v == self._current_voice),
                on_click=lambda _e, voice=v: self._on_voice_select(voice),
            )
            for v in self._voices
        ]

    def _build_voice_menu(self):
        """声音切换菜单（仅当提供声音列表且语音控件可见时显示）。"""
        if not self._voices or not self._show_voice_controls:
            return ft.Container(visible=False)  # 占位，不占布局
        return ft.PopupMenuButton(
            icon=ft.Icons.RECORD_VOICE_OVER_OUTLINED,
            icon_color=c.TEXT_SECONDARY,
            tooltip=f"选择声音（当前：{self._current_voice or '默认'}）",
            items=self._voice_items(),
        )

    def _on_voice_select(self, voice: str) -> None:
        """切换声音：更新勾选与提示，经回调把声音名交给上层（不动 TTSService）。"""
        self._current_voice = voice
        if isinstance(self._voice_menu, ft.PopupMenuButton):
            self._voice_menu.items = self._voice_items()
            self._voice_menu.tooltip = f"选择声音（当前：{voice}）"
            self._voice_menu.update()
        if self._on_voice_change is not None:
            try:
                self._on_voice_change(voice)
            except Exception:  # noqa: BLE001 - 回调异常不影响 UI
                pass

    def _on_mute_click(self, _e: ft.ControlEvent) -> None:
        """翻转全局静音：更新自身图标 + 通知所有订阅者（气泡 🔊 自动隐藏/显示）。"""
        if self._mute_state is None:
            return
        muted = self._mute_state.toggle()
        self._mute_btn.icon = ft.Icons.VOLUME_OFF if muted else ft.Icons.VOLUME_UP
        self._mute_btn.tooltip = "语音：静音" if muted else "语音：开"
        self._mute_btn.update()

    def _build(self) -> None:
        # 透明化：壁纸为全局统一底色，顶栏只保留底部描边做分隔
        self.bgcolor = None
        self.padding = ft.Padding.only(
            left=sp.LG, right=sp.LG, top=layout.HEADER_PAD_Y, bottom=layout.HEADER_PAD_Y
        )
        self.border = ft.Border.only(bottom=ft.BorderSide(width=1, color=c.BORDER))
        self.height = layout.HEADER_HEIGHT
        self.content = ft.Row(
            [
                make_avatar(self._avatar_provider, state_key="calm", radius=20, text_size=16),
                ft.Column(
                    [
                        ft.Text(
                            self.persona.name,
                            color=c.TEXT_PRIMARY,
                            size=t.TITLE,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(
                            self.persona.title or "本地",
                            color=c.TEXT_SECONDARY,
                            size=t.CAPTION,
                        ),
                    ],
                    spacing=2,
                    expand=True,
                ),
                self._status_row,
                self._new_chat_btn,
                self._voice_menu,
                self._mute_btn,
                self._persona_btn,
            ],
            spacing=sp.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def set_thinking(self, thinking: bool) -> None:
        """切换为思考中状态。"""
        if thinking:
            self._status_text.value = "正在思考…"
            self._status_text.color = c.PRIMARY
            self._status_dot = ft.ProgressRing(width=10, height=10, color=c.PRIMARY, stroke_width=2)
        else:
            self._status_text.value = "在线"
            self._status_text.color = c.TEXT_MUTED
            self._status_dot = self._build_dot()
        # 直接替换已保存的状态行引用，避免依赖 Row 子项索引
        self._status_row.controls[0] = self._status_dot
        self._status_row.update()

    def reveal(self) -> None:
        """入场淡入（由 app 在装载后调用）。"""
        self.opacity = 1
        self.update()
