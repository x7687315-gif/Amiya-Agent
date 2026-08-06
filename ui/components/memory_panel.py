"""右栏：记忆档案面板（长期关系的外显，Step 2.6 落地）。

设计要点（严守分层，符合 fullstack-dev「UI 不碰数据层」原则）：
- 面板**只**通过 MemoryManager 取数 / 改数据，绝不直接碰 store 或 SQL。
- 三栏映射：长期记忆(fact/preference/relationship) / 近期事件(event) / 重要目标(goal)。
- 待确认候选队列：用户在此「记住」或「不用记」，全部走人工确认闸门（与 2.7 一致）。
- 每轮对话助手想起的记忆，由 Agent 的 on_retrieval 回调经 app 调度
  set_active_memories(hits)，被选中的条目会被高亮（紫边 + 淡紫底）。
- 记忆功能未启用（MEMORY_ENABLED=0）时显示明确空态，不假装工作。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

import flet as ft

from ui.theme import c, t, sp, r, layout

# 记忆类型 → 展示标签
TYPE_LABEL: Dict[str, str] = {
    "fact": "事实",
    "preference": "偏好",
    "event": "事件",
    "goal": "目标",
    "relationship": "关系",
}

# 记忆类型 → 所在分区（长期记忆 / 近期事件 / 重要目标）
LONG_TERM_TYPES = ("fact", "preference", "relationship")
EVENT_TYPES = ("event",)
GOAL_TYPES = ("goal")


def section_for_type(type: str) -> str:
    """记忆类型归属哪个分区。返回 'long_term' / 'events' / 'goals'。

    抽成模块级纯函数便于单测，UI 与逻辑不耦合 flet。
    """
    if type in LONG_TERM_TYPES:
        return "long_term"
    if type in EVENT_TYPES:
        return "events"
    return "goals"


def _stars(n: int, max_n: int = 5) -> str:
    n = max(0, min(max_n, int(n)))
    return "★" * n + "☆" * (max_n - n)


def _dots(n: int, max_n: int = 5) -> str:
    n = max(0, min(max_n, int(n)))
    return "●" * n + "○" * (max_n - n)


class MemoryPanel(ft.Container):
    """右侧记忆档案面板（Step 2.6 真实数据版）。"""

    def __init__(self, persona, memory: "Optional[object]" = None) -> None:
        super().__init__()
        self.persona = persona
        self.memory = memory  # MemoryManager 实例或 None（未启用）
        self._active_ids: Set[int] = set()

        self._long_term_col = ft.Column(spacing=sp.SM, scroll=ft.ScrollMode.AUTO, expand=True)
        self._events_col = ft.Column(spacing=sp.SM, scroll=ft.ScrollMode.AUTO, expand=True)
        self._goals_col = ft.Column(spacing=sp.SM, scroll=ft.ScrollMode.AUTO, expand=True)
        self._cand_col = ft.Column(spacing=sp.SM)

        self._build()
        self.refresh()

    # ----- 布局 -----
    def _build(self) -> None:
        self.width = layout.RIGHT_COL_WIDTH
        self.bgcolor = c.SURFACE
        self.border = ft.Border.only(left=ft.BorderSide(width=1, color=c.BORDER))
        self.padding = ft.Padding.only(left=sp.LG, right=sp.LG, top=sp.LG, bottom=sp.LG)
        self.content = ft.Column(
            [
                ft.Row(
                    [
                        ft.Text("记忆档案", color=c.TEXT_PRIMARY, size=t.TITLE, weight=ft.FontWeight.BOLD),
                        ft.Container(expand=True),
                        ft.IconButton(
                            icon=ft.Icons.ADD_ROUNDED,
                            icon_color=c.PRIMARY,
                            tooltip="记住一件事",
                            width=32,
                            height=32,
                            on_click=self._open_add,
                        ),
                    ],
                ),
                ft.Container(height=sp.XS),
                self._section("长期记忆", self._long_term_col),
                self._section("近期事件", self._events_col),
                self._section("重要目标", self._goals_col),
                self._section("待确认", self._cand_col),
            ],
            spacing=sp.LG,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _section(self, title: str, body: ft.Control) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(title, color=c.PRIMARY, size=t.TINY, weight=ft.FontWeight.BOLD),
                    body,
                ],
                spacing=sp.SM,
            ),
            bgcolor=c.PRIMARY_SOFT,
            border_radius=r.MD,
            padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.MD, bottom=sp.MD),
        )

    # ----- 数据刷新 -----
    def refresh(self) -> None:
        """从 MemoryManager 重新取数并重建全部分区。

        任何会改数据的操作（确认/否决/遗忘/新增）后都应调用。
        """
        # 清空
        self._long_term_col.controls.clear()
        self._events_col.controls.clear()
        self._goals_col.controls.clear()
        self._cand_col.controls.clear()

        if self.memory is None:
            note = ft.Text(
                "记忆功能未启用\n（在 .env 设置 MEMORY_ENABLED=1 后重启）",
                color=c.TEXT_MUTED,
                size=t.TINY,
                text_align=ft.TextAlign.CENTER,
            )
            self._long_term_col.controls.append(self._muted(note))
            self._safe_update()
            return

        try:
            memories = self.memory.list_memories(limit=200)
            candidates = self.memory.pending_candidates(limit=50)
        except Exception as e:  # noqa: BLE001 - 取数失败不应让面板崩
            note = ft.Text(f"记忆读取失败：{e}", color=c.ERROR, size=t.TINY)
            self._long_term_col.controls.append(self._muted(note))
            self._safe_update()
            return

        long_term = [m for m in memories if section_for_type(m["type"]) == "long_term"]
        events = [m for m in memories if m["type"] == "event"]
        goals = [m for m in memories if m["type"] == "goal"]

        self._long_term_col.controls.extend(
            [self._render_memory(m) for m in long_term] or [self._empty("（暂无长期记忆）")]
        )
        self._events_col.controls.extend(
            [self._render_memory(m) for m in events] or [self._empty("（暂无近期事件）")]
        )
        self._goals_col.controls.extend(
            [self._render_memory(m) for m in goals] or [self._empty("（暂无目标）")]
        )
        self._cand_col.controls.extend(
            [self._render_candidate(cand) for cand in candidates] or [self._empty("（没有待确认的事）")]
        )
        self._safe_update()

    def _safe_update(self) -> None:
        """控件挂到 page 之前（构造期）调用 update() 会抛 RuntimeError，这里兜底。

        Flet 的 `self.page` 属性在控件未挂载时会直接抛异常而非返回 None，
        因此不能用 `if self.page is not None` 判断——改为吞掉挂载前的更新错误。
        page.add 时会把整棵已构建好的子树一次性渲染出来，不丢数据。
        """
        try:
            self.update()
        except RuntimeError:
            pass

    # ----- 渲染：单条记忆 -----
    def _render_memory(self, mem: Dict[str, object]) -> ft.Container:
        mem_id = int(mem["id"])
        type_ = str(mem["type"])
        content = str(mem["content"])
        importance = int(mem.get("importance", 3) or 3)
        confidence = int(mem.get("confidence", 3) or 3)
        active = mem_id in self._active_ids

        badge = ft.Container(
            content=ft.Text(TYPE_LABEL.get(type_, type_), color=c.PRIMARY_DARK, size=t.TINY, weight=ft.FontWeight.BOLD),
            bgcolor=c.PRIMARY_LIGHT,
            border_radius=r.FULL,
            padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=2, bottom=2),
        )
        forget_btn = ft.IconButton(
            icon=ft.Icons.DELETE_OUTLINE,
            icon_size=15,
            icon_color=c.TEXT_MUTED,
            tooltip="遗忘这条",
            width=26,
            height=26,
            on_click=lambda _e, mid=mem_id: self._forget(mid),
        )
        head = ft.Row([badge, ft.Container(expand=True), forget_btn], spacing=sp.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        body = ft.Text(content, color=c.TEXT_PRIMARY, size=t.CAPTION, expand=True)
        meta = ft.Row(
            [
                ft.Text(f"重要 {_stars(importance)}", color=c.TEXT_SECONDARY, size=t.TINY),
                ft.Container(expand=True),
                ft.Text(f"置信 {_dots(confidence)}", color=c.TEXT_MUTED, size=t.TINY),
            ],
            spacing=sp.SM,
        )
        inner = ft.Column([head, body, meta], spacing=sp.XS)

        return ft.Container(
            content=inner,
            padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=sp.SM, bottom=sp.SM),
            border_radius=r.SM,
            border=ft.Border(left=ft.BorderSide(3, c.PRIMARY)) if active else None,
            bgcolor=c.PRIMARY_SOFT if active else None,
        )

    # ----- 渲染：待确认候选 -----
    def _render_candidate(self, cand: Dict[str, object]) -> ft.Container:
        cand_id = int(cand["id"])
        content = str(cand["content"])
        reason = str(cand.get("reason") or "")
        type_ = str(cand.get("type", ""))
        badge = ft.Container(
            content=ft.Text(TYPE_LABEL.get(type_, type_), color=c.PRIMARY_DARK, size=t.TINY, weight=ft.FontWeight.BOLD),
            bgcolor=c.PRIMARY_LIGHT,
            border_radius=r.FULL,
            padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=2, bottom=2),
        )
        head = ft.Row([badge, ft.Container(expand=True)], spacing=sp.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        body = ft.Text(content, color=c.TEXT_PRIMARY, size=t.CAPTION, expand=True)
        rows: List[ft.Control] = [head, body]
        if reason:
            rows.append(ft.Text(f"为什么记得：{reason}", color=c.TEXT_MUTED, size=t.TINY))
        actions = ft.Row(
            [
                ft.TextButton("记住", style=ft.ButtonStyle(color=c.PRIMARY), on_click=lambda _e, cid=cand_id: self._confirm(cid)),
                ft.TextButton("不用记", style=ft.ButtonStyle(color=c.TEXT_MUTED), on_click=lambda _e, cid=cand_id: self._reject(cid)),
            ],
            spacing=sp.XS,
        )
        rows.append(actions)
        return ft.Container(
            content=ft.Column(rows, spacing=sp.XS),
            padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=sp.SM, bottom=sp.SM),
            border_radius=r.SM,
            bgcolor=c.SURFACE_SECONDARY,
        )

    # ----- 空态 / 占位 -----
    def _empty(self, text: str) -> ft.Container:
        return self._muted(ft.Text(text, color=c.TEXT_MUTED, size=t.TINY))

    @staticmethod
    def _muted(ctrl: ft.Control) -> ft.Container:
        return ft.Container(content=ctrl, padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=sp.XS, bottom=sp.XS))

    # ----- 用户操作（全部收敛到 MemoryManager）-----
    def _forget(self, mem_id: int) -> None:
        if self.memory is None:
            return
        try:
            self.memory.forget_id(mem_id)
        except Exception as e:  # noqa: BLE001
            print(f"[memory-panel] 遗忘失败: {e}")
        self.refresh()

    def _confirm(self, cand_id: int) -> None:
        if self.memory is None:
            return
        try:
            self.memory.confirm_candidate(cand_id)
        except Exception as e:  # noqa: BLE001
            print(f"[memory-panel] 确认候选失败: {e}")
        self.refresh()

    def _reject(self, cand_id: int) -> None:
        if self.memory is None:
            return
        try:
            self.memory.reject_candidate(cand_id)
        except Exception as e:  # noqa: BLE001
            print(f"[memory-panel] 否决候选失败: {e}")
        self.refresh()

    # ----- 本轮检索高亮（由 Agent.on_retrieval 经 app 调度）-----
    def set_active_memories(self, hits) -> None:
        """高亮本轮助手想起的记忆。传入空列表即清除高亮。

        直接重建分区以应用高亮——数据量小，成本可忽略。
        """
        self._active_ids = {int(h.id) for h in hits}
        self.refresh()

    # ----- 新增记忆对话框 -----
    def _open_add(self, _e: ft.ControlEvent) -> None:
        if self.memory is None or self.page is None:
            return
        content_field = ft.TextField(
            label="助手该记住什么？",
            hint_text="例如：用户喜欢在周末去爬山",
            text_style=ft.TextStyle(color=c.TEXT_PRIMARY, size=t.BODY),
            bgcolor=c.SURFACE_SECONDARY,
            border=ft.InputBorder.NONE,
            border_radius=r.SM,
            content_padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=10, bottom=10),
            multiline=True,
            min_lines=1,
            max_lines=3,
            autofocus=True,
        )
        type_dd = ft.Dropdown(
            label="类型",
            value="fact",
            options=[ft.dropdown.Option(key=k, text=TYPE_LABEL[k]) for k in ("fact", "preference", "event", "goal", "relationship")],
            text_size=t.CAPTION,
            border=ft.InputBorder.OUTLINE,
            border_color=c.BORDER,
            color=c.TEXT_PRIMARY,
        )

        def _submit(_ev: ft.ControlEvent) -> None:
            text = (content_field.value or "").strip()
            if not text:
                content_field.error_text = "说点什么再让助手记。"
                content_field.update()
                return
            try:
                self.memory.remember(str(type_dd.value or "fact"), text, importance=5, confidence=4)
            except Exception as e:  # noqa: BLE001
                print(f"[memory-panel] 新增记忆失败: {e}")
            dialog.open = False
            self.page.update()
            self.refresh()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("让助手记住一件事", size=t.BODY, weight=ft.FontWeight.BOLD),
            content=ft.Column([content_field, ft.Container(height=sp.SM), type_dd], spacing=sp.XS, tight=True, width=320),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self._close_dialog(dialog)),
                ft.FilledButton("记住", on_click=_submit),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.overlay.append(dialog)
        dialog.open = True
        self.page.update()

    def _close_dialog(self, dialog: ft.AlertDialog) -> None:
        dialog.open = False
        if self.page is not None:
            self.page.update()
