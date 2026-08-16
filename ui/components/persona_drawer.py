"""人格抽屉组件：从右侧滑出，展示助手的设定。

Phase 3 起：抽屉从 persona 子系统读取配置——
- 视角与认知 / 价值观 / 思考方式：identity.yaml（persona.identity）
- 行为准则：behavior.yaml
- 与用户的关系：relationship.yaml（默认阶段背景）

皮肤计划 Phase 2：底部新增「外观」区块——皮肤缩略图网格 + 「跟随情绪」
开关。选中只写配置（重启后生效），不做运行时换肤（Phase 4 边界）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import flet as ft

from core.persona.loader import load_behavior, load_relationship
from ui.theme import ALIGN_CENTER, c, t, sp, r
from ui.components.avatar import make_avatar
from ui.design.avatar_provider import AvatarProvider
from ui.design.skin import Skin


class PersonaDrawer(ft.NavigationDrawer):
    """右侧人格信息抽屉。"""

    def __init__(
        self,
        persona,
        avatar_provider: AvatarProvider | None = None,
        skins: Optional[List[Skin]] = None,
        ui_skin: str = "auto",
        current_skin_id: str = "starry",
        on_skin_selected: Optional[Callable[[str], None]] = None,
    ) -> None:
        """ skins: 已加载皮肤列表（SkinManager 注册表），None/空 = 不显示「外观」区块。
            ui_skin: 当前配置（"auto" 或皮肤 id），决定缩略图选中态与开关初值。
            current_skin_id: 本次启动实际解析出的皮肤 id（「跟随情绪」关闭时锁定为它）。
            on_skin_selected: 选中回调，参数为皮肤 id 或 "auto"（持久化归 app 层）。
        """
        super().__init__()
        self.persona = persona
        self._avatar_provider = avatar_provider
        self._skins = list(skins) if skins else []
        self._ui_skin = ui_skin or "auto"
        self._current_skin_id = current_skin_id
        self._on_skin_selected = on_skin_selected
        self._auto_switch: Optional[ft.Switch] = None
        self._skin_status: Optional[ft.Text] = None
        self._thumb_by_id: Dict[str, ft.Container] = {}
        self.bgcolor = c.SURFACE
        self.controls = self._build_content()

    def _build_content(self) -> List[ft.Control]:
        identity = self.persona.identity
        tags = self._extract_tags(identity)
        sections = [
            ("视角与认知", "perspective"),
            ("价值观", "values"),
            ("思考方式", "thinking_style"),
        ]

        controls: List[ft.Control] = [
            ft.Container(
                content=ft.Row(
                    [
                        ft.Text("关于助手", size=t.HEADLINE, weight=ft.FontWeight.BOLD, color=c.TEXT_PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                padding=ft.Padding.only(left=sp.XL, right=sp.MD, top=sp.XL, bottom=sp.LG),
            ),
            ft.Container(
                content=ft.Column(
                    [
                        make_avatar(
                            self._avatar_provider,
                            state_key="calm",
                            radius=36,
                            text_size=24,
                            bgcolor=c.PRIMARY_LIGHT,
                            text_color=c.PRIMARY,
                        ),
                        ft.Text(
                            self.persona.name,
                            size=20,
                            weight=ft.FontWeight.BOLD,
                            color=c.TEXT_PRIMARY,
                        ),
                        ft.Text(
                            self.persona.title or "",
                            size=t.CAPTION,
                            color=c.TEXT_SECONDARY,
                        ),
                    ],
                    spacing=sp.SM,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=ft.Padding.only(left=sp.XL, right=sp.XL, bottom=sp.LG),
                alignment=ALIGN_CENTER,
            ),
        ]

        if tags:
            controls.append(
                ft.Container(
                    content=ft.Row(
                        [self._tag(label) for label in tags],
                        spacing=sp.SM,
                        alignment=ft.MainAxisAlignment.CENTER,
                        wrap=True,
                    ),
                    padding=ft.Padding.only(left=sp.XL, right=sp.XL, bottom=sp.LG),
                )
            )

        for title, key in sections:
            section_text = self._format_list(identity.get(key))
            if section_text:
                controls.append(self._section(title, section_text))

        # 行为准则（来自 behavior.yaml）
        behavior = load_behavior().get("guidelines", [])
        if behavior:
            controls.append(self._section("行为准则", self._format_list(behavior)))

        # 与用户的关系（来自 relationship.yaml 默认阶段背景）
        rel = load_relationship()
        rel_stage = rel.get("default_stage", "信任")
        rel_bg = rel.get("stages", {}).get(rel_stage, {}).get("background", [])
        if rel_bg:
            controls.append(
                self._section(f"与用户的关系（{rel_stage}）", self._format_list(rel_bg))
            )

        # 皮肤计划 Phase 2：外观区块（无皮肤列表时不显示，兼容旧调用方与测试）
        if self._skins:
            controls.append(self._build_appearance_section())

        controls.append(ft.Container(height=sp.XL))
        return controls

    # ----- 外观（皮肤选择，Phase 2） -----
    def _build_appearance_section(self) -> ft.Container:
        self._thumb_by_id = {}
        for skin in self._skins:
            selected = self._ui_skin not in ("", "auto") and skin.id == self._ui_skin
            thumb = ft.Container(
                content=ft.Column(
                    [
                        ft.Image(
                            src=str(skin.avatar_path),
                            width=64,
                            height=64,
                            fit=ft.BoxFit.COVER,
                            border_radius=32,
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
                border=ft.Border.all(
                    width=2, color=c.PRIMARY if selected else c.BORDER
                ),
                border_radius=r.MD,
                padding=ft.Padding.only(left=6, right=6, top=8, bottom=4),
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
                    ft.Text("外观", size=t.TINY, weight=ft.FontWeight.BOLD, color=c.PRIMARY),
                    ft.Row(
                        list(self._thumb_by_id.values()),
                        wrap=True,
                        spacing=sp.SM,
                        run_spacing=sp.SM,
                    ),
                    self._auto_switch,
                    ft.Text(
                        "点击即切换；跟随情绪时按助手近 24 小时的状态自动挑选。",
                        size=t.TINY,
                        color=c.TEXT_MUTED,
                    ),
                    self._skin_status,
                ],
                spacing=sp.SM,
            ),
            padding=ft.Padding.only(left=sp.XL, right=sp.XL, bottom=sp.LG),
        )

    def _handle_skin_click(self, skin_id: str) -> None:
        """点缩略图：手动锁定该皮肤（自动退出跟随情绪），持久化归回调方。"""
        if self._on_skin_selected is not None:
            self._on_skin_selected(skin_id)
        if self._auto_switch is not None:
            self._auto_switch.value = False
            self._try_update(self._auto_switch)
        self._refresh_selection(skin_id)
        self._set_skin_status(f"已切换为「{self._skin_name(skin_id)}」。")

    def _handle_auto_toggle(self, e: ft.ControlEvent) -> None:
        """跟随情绪开关：开 = auto；关 = 锁定本次启动解析出的当前皮肤。"""
        follow = bool(getattr(getattr(e, "control", None), "value", False))
        skin_id = "auto" if follow else self._current_skin_id
        if self._on_skin_selected is not None:
            self._on_skin_selected(skin_id)
        self._refresh_selection("auto" if follow else self._current_skin_id)
        self._set_skin_status(
            "已切换为跟随情绪。"
            if follow
            else f"已锁定「{self._skin_name(self._current_skin_id)}」。"
        )

    def _refresh_selection(self, selected_id: str) -> None:
        for sid, thumb in self._thumb_by_id.items():
            thumb.border = ft.Border.all(
                width=2, color=c.PRIMARY if sid == selected_id else c.BORDER
            )
            self._try_update(thumb)

    def _set_skin_status(self, message: str) -> None:
        if self._skin_status is not None:
            self._skin_status.value = message
            self._try_update(self._skin_status)

    def _skin_name(self, skin_id: str) -> str:
        for skin in self._skins:
            if skin.id == skin_id:
                return skin.name
        return skin_id

    @staticmethod
    def _try_update(ctrl: ft.Control) -> None:
        try:
            ctrl.update()
        except Exception:  # noqa: BLE001 - 未挂载时忽略（离线测试场景）
            pass

    def _section(self, title: str, text: str) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, size=t.TINY, weight=ft.FontWeight.BOLD, color=c.PRIMARY),
                    ft.Text(text, size=t.BODY, color=c.TEXT_PRIMARY),
                ],
                spacing=sp.SM,
            ),
            padding=ft.Padding.only(left=sp.XL, right=sp.XL, bottom=sp.LG),
        )

    def _tag(self, label: str) -> ft.Container:
        return ft.Container(
            content=ft.Text(label, size=t.TINY, color=c.TEXT_SECONDARY),
            bgcolor=c.SURFACE_SECONDARY,
            border_radius=r.SM,
            padding=ft.Padding.only(left=sp.SM + 4, right=sp.SM + 4, top=sp.XS, bottom=sp.XS),
        )

    def _extract_tags(self, identity: Dict[str, Any]) -> List[str]:
        """从 behavior.yaml 行为准则 + identity 价值观中提取行为标签。"""
        tags: List[str] = []
        guidelines = load_behavior().get("guidelines", [])
        values = identity.get("values", [])
        keywords = {
            "温柔": "温柔",
            "认真": "认真",
            "责任": "责任感",
            "担当": "有担当",
            "不放弃": "坚韧",
            "诚实": "诚实",
            "透明": "透明",
            "沟通": "善于沟通",
        }
        for line in list(guidelines) + list(values):
            if not isinstance(line, str):
                continue
            for kw, tag in keywords.items():
                if kw in line and tag not in tags:
                    tags.append(tag)
        return tags[:6]

    def _format_list(self, value: Any) -> str:
        if isinstance(value, list):
            return "\n".join(f"· {item}" for item in value if isinstance(item, str))
        if isinstance(value, str):
            return value
        return ""
