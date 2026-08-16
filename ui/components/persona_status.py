"""左栏：角色状态面板（Presence 主战场）。

展示助手的在场证据：头像与身份、当前情绪状态、信赖度、今日陪伴时长、最近记忆。
v2 数据为占位/规则启发式，结构先行；后续接 PersonaRuntime / Memory 层即替换数据源。

左下角「更换皮肤」入口（用户要求：显式可见，不放抽屉）：点击展开皮肤缩略图
网格 + 跟随情绪开关；选中写配置、重启生效（运行时换肤仍是 Phase 4 边界）。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

import flet as ft

from ui.theme import c, t, sp, r, layout, anim, EMOTIONS, DEFAULT_EMOTION
from ui.components.avatar import make_avatar
from ui.design.avatar_provider import AvatarProvider
from ui.design.skin import Skin


class PersonaStatusPanel(ft.Container):
    """左侧角色状态栏。"""

    def __init__(
        self,
        persona,
        avatar_provider: AvatarProvider | None = None,
        skins: Optional[List[Skin]] = None,
        ui_skin: str = "auto",
        current_skin_id: str = "starry",
        on_skin_selected: Optional[Callable[[str], None]] = None,
    ) -> None:
        """skins 注入时左下角显示「更换皮肤」入口；否则完全兼容旧行为。"""
        super().__init__()
        self.persona = persona
        self._avatar_provider = avatar_provider
        self._skins = list(skins) if skins else []
        self._ui_skin = ui_skin or "auto"
        self._current_skin_id = current_skin_id
        self._on_skin_selected = on_skin_selected
        self._skin_toggle_btn: Optional[ft.Control] = None
        self._skin_panel: Optional[ft.Control] = None
        self._skin_status: Optional[ft.Text] = None
        self._auto_switch: Optional[ft.Switch] = None
        self._thumb_by_id: Dict[str, ft.Container] = {}
        self._state_key = DEFAULT_EMOTION
        self._trust = 70  # 占位信赖度（Phase 2 接 PersonaRuntime）
        self._companionship_minutes = 0
        self._recent: List[str] = [
            "用户完成了助手架构设计",
            "正在调整作息",
            "计划香港旅行",
        ]  # 占位最近记忆（Phase 2 接 Memory 层）

        self._state_emoji = ft.Text("", size=22)
        self._state_label = ft.Text("", color=c.TEXT_PRIMARY, size=t.CAPTION, weight=ft.FontWeight.W_500)
        self._state_dot = ft.Container(width=8, height=8, border_radius=r.FULL, bgcolor=c.STATE_CALM)
        self._trust_bar = ft.ProgressBar(
            value=self._trust / 100, color=c.TRUST_HIGH, bgcolor=c.TRUST_LOW, height=8
        )
        self._trust_label = ft.Text(f"{self._trust}%", color=c.TEXT_SECONDARY, size=t.TINY)
        self._companionship_label = ft.Text(
            "已经陪伴 0 分钟", color=c.TEXT_SECONDARY, size=t.CAPTION
        )
        self._recent_col = ft.Column(spacing=sp.SM)

        self._build()
        self.set_state(self._state_key)  # 初始化状态显示

    def _build(self) -> None:
        self.width = layout.LEFT_COL_WIDTH
        # 透明化：壁纸为全局统一底色，内容卡片自带浅底保证可读
        self.bgcolor = None
        self.border = ft.Border.only(right=ft.BorderSide(width=1, color=c.BORDER))
        self.padding = ft.Padding.only(left=sp.LG, right=sp.LG, top=sp.LG, bottom=sp.LG)

        identity = ft.Row(
            [
                make_avatar(self._avatar_provider, state_key="calm", radius=26, text_size=18),
                ft.Column(
                    [
                        ft.Text(self.persona.name, color=c.TEXT_PRIMARY, size=t.TITLE, weight=ft.FontWeight.BOLD),
                        ft.Text(self.persona.title or "本地", color=c.TEXT_SECONDARY, size=t.TINY),
                    ],
                    spacing=2,
                    expand=True,
                ),
            ],
            spacing=sp.MD,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        column_items = [
            identity,
            self._section_state(),
            self._section_trust(),
            self._section_companionship(),
            self._section_recent(),
        ]
        if self._skins:
            # 左下角显式切肤入口（默认收起，点击展开网格）
            self._skin_toggle_btn = ft.Container(
                content=ft.Row(
                    [
                        ft.Icon(ft.Icons.PALETTE_ROUNDED, size=16, color=c.PRIMARY),
                        ft.Text("更换皮肤", color=c.PRIMARY, size=t.CAPTION, weight=ft.FontWeight.W_500),
                    ],
                    spacing=sp.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                bgcolor=c.PRIMARY_SOFT,
                border=ft.Border.all(width=1, color=c.BORDER),
                border_radius=r.MD,
                padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.SM + 2, bottom=sp.SM + 2),
                tooltip="选择外观皮肤（点击即切换）",
                on_click=lambda _e: self._toggle_skin_panel(),
            )
            self._skin_panel = self._build_skin_panel()
            column_items += [ft.Container(expand=True), self._skin_toggle_btn, self._skin_panel]

        self.content = ft.Column(
            column_items,
            spacing=sp.LG,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _card(self, children: List[ft.Control]) -> ft.Container:
        return ft.Container(
            content=ft.Column(children, spacing=sp.SM),
            bgcolor=c.PRIMARY_SOFT,
            border_radius=r.MD,
            padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.MD, bottom=sp.MD),
        )

    def _section_state(self) -> ft.Container:
        return self._card(
            [
                ft.Text("当前状态", color=c.TEXT_MUTED, size=t.TINY, weight=ft.FontWeight.BOLD),
                ft.Row(
                    [self._state_dot, self._state_emoji, self._state_label],
                    spacing=sp.SM,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ]
        )

    def _section_trust(self) -> ft.Container:
        return self._card(
            [
                ft.Row(
                    [
                        ft.Text("信赖度", color=c.TEXT_MUTED, size=t.TINY, weight=ft.FontWeight.BOLD),
                        ft.Container(expand=True),
                        self._trust_label,
                    ],
                ),
                self._trust_bar,
            ]
        )

    def _section_companionship(self) -> ft.Container:
        return self._card(
            [
                ft.Text("今日陪伴", color=c.TEXT_MUTED, size=t.TINY, weight=ft.FontWeight.BOLD),
                self._companionship_label,
            ]
        )

    def _section_recent(self) -> ft.Container:
        self._render_recent()
        return self._card(
            [
                ft.Text("最近记忆", color=c.TEXT_MUTED, size=t.TINY, weight=ft.FontWeight.BOLD),
                self._recent_col,
            ]
        )

    def _render_recent(self) -> None:
        self._recent_col.controls = [
            ft.Row(
                [
                    ft.Container(width=4, height=4, bgcolor=c.PRIMARY, border_radius=r.FULL, margin=ft.Margin.only(top=6)),
                    ft.Text(item, color=c.TEXT_SECONDARY, size=t.CAPTION, expand=True),
                ],
                spacing=sp.SM,
                alignment=ft.MainAxisAlignment.START,
            )
            for item in self._recent
        ]

    # ----- 左下角皮肤入口 -----
    def _toggle_skin_panel(self) -> None:
        """展开 / 收起皮肤网格。"""
        if self._skin_panel is None:
            return
        self._skin_panel.visible = not self._skin_panel.visible
        self._safe_update(self._skin_panel)

    def _build_skin_panel(self) -> ft.Container:
        """展开区：皮肤缩略图网格 + 跟随情绪开关 + 状态提示（默认收起）。"""
        self._thumb_by_id = {}
        for skin in self._skins:
            selected = self._ui_skin not in ("", "auto") and skin.id == self._ui_skin
            thumb = ft.Container(
                content=ft.Column(
                    [
                        ft.Image(
                            src=str(skin.avatar_path),
                            width=52,
                            height=52,
                            fit=ft.BoxFit.COVER,
                            border_radius=26,
                        ),
                        ft.Text(
                            skin.name,
                            size=t.TINY,
                            color=c.TEXT_SECONDARY,
                            text_align=ft.TextAlign.CENTER,
                        ),
                    ],
                    spacing=4,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                border=ft.Border.all(width=2, color=c.PRIMARY if selected else c.BORDER),
                border_radius=r.MD,
                padding=ft.Padding.only(left=4, right=4, top=6, bottom=4),
                tooltip=skin.description or skin.name,
                on_click=lambda _e, sid=skin.id: self._handle_skin_click(sid),
            )
            self._thumb_by_id[skin.id] = thumb

        self._auto_switch = ft.Switch(
            label="跟随情绪",
            value=self._ui_skin == "auto",
            active_color=c.PRIMARY,
            on_change=self._handle_auto_toggle,
        )
        self._skin_status = ft.Text("", size=t.TINY, color=c.TEXT_MUTED)

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        list(self._thumb_by_id.values()),
                        wrap=True,
                        spacing=sp.SM,
                        run_spacing=sp.SM,
                    ),
                    self._auto_switch,
                    self._skin_status,
                ],
                spacing=sp.SM,
                horizontal_alignment=ft.CrossAxisAlignment.START,
            ),
            bgcolor=c.SURFACE,
            border=ft.Border.all(width=1, color=c.BORDER),
            border_radius=r.MD,
            padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.MD, bottom=sp.MD),
            visible=False,
        )

    def _handle_skin_click(self, skin_id: str) -> None:
        if self._on_skin_selected is not None:
            self._on_skin_selected(skin_id)
        if self._auto_switch is not None:
            self._auto_switch.value = False
            self._safe_update(self._auto_switch)
        self._refresh_selection(skin_id)
        self._set_skin_status(f"已切换为「{self._skin_name_of(skin_id)}」。")

    def _handle_auto_toggle(self, e: ft.ControlEvent) -> None:
        follow = bool(getattr(getattr(e, "control", None), "value", False))
        skin_id = "auto" if follow else self._current_skin_id
        if self._on_skin_selected is not None:
            self._on_skin_selected(skin_id)
        self._refresh_selection("auto" if follow else self._current_skin_id)
        self._set_skin_status(
            "已切换为跟随情绪。" if follow else "已锁定当前皮肤。"
        )

    def _skin_name_of(self, skin_id: str) -> str:
        for skin in self._skins:
            if skin.id == skin_id:
                return skin.name
        return skin_id

    def _refresh_selection(self, selected_id: str) -> None:
        for sid, thumb in self._thumb_by_id.items():
            thumb.border = ft.Border.all(
                width=2, color=c.PRIMARY if sid == selected_id else c.BORDER
            )
            self._safe_update(thumb)

    def _set_skin_status(self, message: str) -> None:
        if self._skin_status is not None:
            self._skin_status.value = message
            self._safe_update(self._skin_status)

    # —— 对外更新接口（后续接真实数据源）——
    @staticmethod
    def _safe_update(ctrl: ft.Control) -> None:
        """控件未挂载到 page 时 update() 会抛 RuntimeError，安全忽略。"""
        try:
            ctrl.update()
        except Exception:  # noqa: BLE001 - 离线/未挂载场景统一忽略
            pass

    def set_state(self, state_key: str) -> None:
        """切换情绪状态（calm/thinking/worried/happy）。"""
        if state_key not in EMOTIONS:
            state_key = DEFAULT_EMOTION
        self._state_key = state_key
        emo = EMOTIONS[state_key]
        self._state_emoji.value = emo.emoji
        self._state_label.value = emo.label
        self._state_dot.bgcolor = emo.color
        self._safe_update(self._state_dot)
        self._safe_update(self._state_emoji)
        self._safe_update(self._state_label)

    def set_trust(self, value: int) -> None:
        """设置信赖度（0–100）。"""
        self._trust = max(0, min(100, int(value)))
        self._trust_bar.value = self._trust / 100
        self._trust_label.value = f"{self._trust}%"
        self._safe_update(self._trust_bar)
        self._safe_update(self._trust_label)

    def set_companionship_minutes(self, minutes: int) -> None:
        self._companionship_minutes = max(0, int(minutes))
        self._companionship_label.value = f"已经陪伴 {self._companionship_minutes} 分钟"
        self._safe_update(self._companionship_label)

    def set_recent_memories(self, items: List[str]) -> None:
        self._recent = list(items)
        self._render_recent()
        self._safe_update(self._recent_col)
