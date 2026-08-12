"""右侧栏：记忆管理中心（Memory Management Center，M3 重新定义）。

设计要点（严守分层，符合 fullstack-dev「UI 不碰数据层」原则）：
- 面板**只**通过 MemoryManager 取数 / 改数据，绝不直接碰 store 或 SQL。
- 核心理念：「AI 提议，人拥有最终控制权」。本面板是这一理念的载体——
  AI 只在 memory_candidate 里「提议」，人通过 M3.2 的「确认 / 修改 / 拒绝」
  三操作拍板；确认前的记忆结构上不可被检索（见 2.7 设计）。
- M3.1 候选查看：pending 候选陈列（类型 / 内容 / 为什么记得 / 权重）。
- M3.2 三个操作：确认（转正） / 修改（改 AI 草稿，仍 pending） / 拒绝（丢弃）。
- M3.3 我的记忆：已确认记忆按 事实 / 偏好 / 目标 / 经历 / 关系 五类明文陈列，
  解决陪伴型 AI 最大隐患——用户不知道 AI 记住了什么。每条可修改 / 遗忘。
- 记忆功能未启用（MEMORY_ENABLED=0）时显示明确空态，不假装工作。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Set, Tuple

import flet as ft

from ui.theme import c, t, sp, r, layout

# 记忆类型 → 展示标签
TYPE_LABEL: Dict[str, str] = {
    "fact": "事实",
    "preference": "偏好",
    "event": "经历",
    "goal": "目标",
    "relationship": "关系",
}

# 我的记忆：五分类陈列顺序（事实 / 偏好 / 目标 / 经历 / 关系）
CENTER_CATEGORIES: List[Tuple[str, str]] = [
    ("fact", "事实"),
    ("preference", "偏好"),
    ("goal", "目标"),
    ("event", "经历"),
    ("relationship", "关系"),
]

# 抽取类型下拉选项（与 MEMORY_TYPES 一致）
_CANDIDATE_TYPES = ("fact", "preference", "event", "goal", "relationship")


class MemoryPanel(ft.Container):
    """右侧记忆管理中心（M3）。"""

    def __init__(
        self,
        persona,
        memory: "Optional[object]" = None,
        on_extract: "Optional[Callable[[], int]]" = None,
    ) -> None:
        super().__init__()
        self.persona = persona
        self.memory = memory  # MemoryManager 实例或 None（未启用）
        self._on_extract = on_extract  # 可选：点击「让助手整理候选」时调用，返回新候选数
        self._active_ids: Set[int] = set()

        # 候选队列 + 我的记忆五栏
        self._cand_col = ft.Column(spacing=sp.SM)
        self._cat_cols: Dict[str, ft.Column] = {
            type_: ft.Column(spacing=sp.SM) for type_, _ in CENTER_CATEGORIES
        }
        # 候选栏里的「整理候选」按钮（仅 extractor 可用时显示）
        self._extract_btn: Optional[ft.Control] = None

        self._build()
        self.refresh()

    # ----- 布局 -----
    def _build(self) -> None:
        self.width = layout.RIGHT_COL_WIDTH
        self.bgcolor = c.SURFACE
        self.border = ft.Border.only(left=ft.BorderSide(width=1, color=c.BORDER))
        self.padding = ft.Padding.only(left=sp.LG, right=sp.LG, top=sp.LG, bottom=sp.LG)

        header_title = ft.Column(
            [
                ft.Text("记忆管理中心", color=c.TEXT_PRIMARY, size=t.TITLE, weight=ft.FontWeight.BOLD),
                ft.Text("Memory Management Center", color=c.TEXT_MUTED, size=t.TINY),
            ],
            spacing=2,
        )

        header_actions: List[ft.Control] = []
        if self._on_extract is not None:
            self._extract_btn = ft.TextButton(
                "让助手整理候选",
                style=ft.ButtonStyle(color=c.PRIMARY),
                tooltip="基于最近对话，由助手提议候选，你来决定是否记住（AI 只提议）",
                on_click=self._on_extract_click,
            )
            header_actions.append(self._extract_btn)
        header_actions.append(
            ft.IconButton(
                icon=ft.Icons.ADD_ROUNDED,
                icon_color=c.PRIMARY,
                tooltip="记住一件事（你主动告诉助手）",
                width=32,
                height=32,
                on_click=self._open_add,
            )
        )

        header = ft.Row(
            [header_title, ft.Container(expand=True), *header_actions],
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # 待你确认（M3.1 + M3.2）
        candidate_body = ft.Column(
            [self._philosophy_banner(), self._cand_col],
            spacing=sp.SM,
        )
        candidate_section = ft.Container(
            content=ft.Column(
                [
                    ft.Text("待你确认", color=c.PRIMARY, size=t.TINY, weight=ft.FontWeight.BOLD),
                    candidate_body,
                ],
                spacing=sp.SM,
            ),
            bgcolor=c.PRIMARY_SOFT,
            border_radius=r.MD,
            padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.MD, bottom=sp.MD),
        )

        # 我的记忆（M3.3）：五分类分栏
        my_memory_label = ft.Text("我的记忆", color=c.TEXT_PRIMARY, size=t.TINY, weight=ft.FontWeight.BOLD)
        my_memory_sections = [
            self._section_catalog(label, self._cat_cols[type_])
            for type_, label in CENTER_CATEGORIES
        ]

        self.content = ft.Column(
            [header, ft.Container(height=sp.XS), candidate_section, ft.Container(height=sp.MD),
             my_memory_label, ft.Container(height=sp.XS), *my_memory_sections],
            spacing=sp.LG,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _philosophy_banner(self) -> ft.Container:
        """理念横幅：把「AI 提议，人拥有最终控制权」显式写在候选区顶部。"""
        return ft.Container(
            content=ft.Text(
                "助手会提议该记住什么，但每一条都由你拍板——确认、修改或拒绝。",
                color=c.TEXT_SECONDARY,
                size=t.TINY,
            ),
            bgcolor=c.SURFACE_SECONDARY,
            border_radius=r.SM,
            padding=ft.Padding.only(left=sp.SM, right=sp.SM, top=sp.XS, bottom=sp.XS),
        )

    def _section_catalog(self, title: str, body: ft.Control) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [ft.Text(title, color=c.PRIMARY_DARK, size=t.TINY, weight=ft.FontWeight.BOLD), body],
                spacing=sp.XS,
            ),
            bgcolor=c.SURFACE_SECONDARY,
            border_radius=r.MD,
            padding=ft.Padding.only(left=sp.MD, right=sp.MD, top=sp.SM, bottom=sp.SM),
        )

    # ----- 数据刷新 -----
    def refresh(self) -> None:
        """从 MemoryManager 重新取数并重建候选区与五分类区。

        任何会改数据的操作（确认/修改/拒绝/遗忘/新增）后都应调用。
        """
        self._cand_col.controls.clear()
        for col in self._cat_cols.values():
            col.controls.clear()

        if self.memory is None:
            note = ft.Text(
                "记忆功能未启用\n（在 .env 设置 MEMORY_ENABLED=1 后重启）",
                color=c.TEXT_MUTED,
                size=t.TINY,
                text_align=ft.TextAlign.CENTER,
            )
            self._cand_col.controls.append(self._muted(note))
            for col in self._cat_cols.values():
                col.controls.append(self._empty("（未启用）"))
            self._safe_update()
            return

        try:
            memories = self.memory.list_memories(limit=200)
            candidates = self.memory.pending_candidates(limit=50)
        except Exception as e:  # noqa: BLE001 - 取数失败不应让面板崩
            note = ft.Text(f"记忆读取失败：{e}", color=c.ERROR, size=t.TINY)
            self._cand_col.controls.append(self._muted(note))
            self._safe_update()
            return

        for type_ in self._cat_cols:
            items = [m for m in memories if str(m.get("type")) == type_]
            self._cat_cols[type_].controls.extend(
                [self._render_memory(m) for m in items] or [self._empty("（暂无）")]
            )

        self._cand_col.controls.extend(
            [self._render_candidate(cand) for cand in candidates] or [self._empty("（没有待确认的事，助手目前没有提议）")]
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

    # ----- 渲染：单条已确认记忆（M3.3） -----
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
        edit_btn = ft.IconButton(
            icon=ft.Icons.EDIT_OUTLINED,
            icon_size=15,
            icon_color=c.TEXT_MUTED,
            tooltip="修改这条记忆",
            width=26,
            height=26,
            on_click=lambda _e, m=mem: self._open_modify_memory(m),
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
        head = ft.Row([badge, ft.Container(expand=True), edit_btn, forget_btn],
                      spacing=sp.SM, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        body = ft.Text(content, color=c.TEXT_PRIMARY, size=t.CAPTION, expand=True)
        meta = ft.Row(
            [
                ft.Text(f"重要 {importance}/10", color=c.TEXT_SECONDARY, size=t.TINY),
                ft.Container(expand=True),
                ft.Text(f"置信 {confidence}/5", color=c.TEXT_MUTED, size=t.TINY),
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

    # ----- 渲染：待确认候选（M3.1 + M3.2） -----
    def _render_candidate(self, cand: Dict[str, object]) -> ft.Container:
        cand_id = int(cand["id"])
        content = str(cand["content"])
        reason = str(cand.get("reason") or "")
        type_ = str(cand.get("type", ""))
        importance = int(cand.get("importance", 3) or 3)
        confidence = int(cand.get("confidence", 3) or 3)

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
        rows.append(
            ft.Row(
                [
                    ft.Text(f"重要 {importance}/10", color=c.TEXT_SECONDARY, size=t.TINY),
                    ft.Text(f"置信 {confidence}/5", color=c.TEXT_MUTED, size=t.TINY),
                ],
                spacing=sp.MD,
            )
        )
        # M3.2：确认 / 修改 / 拒绝 三操作，缺一不可
        actions = ft.Row(
            [
                ft.FilledButton(
                    "确认",
                    style=ft.ButtonStyle(color=c.ON_PRIMARY, bgcolor=c.PRIMARY),
                    on_click=lambda _e, cid=cand_id: self._confirm(cid),
                ),
                ft.TextButton("修改", style=ft.ButtonStyle(color=c.PRIMARY), on_click=lambda _e, cd=cand: self._open_modify_candidate(cd)),
                ft.TextButton("拒绝", style=ft.ButtonStyle(color=c.TEXT_MUTED), on_click=lambda _e, cid=cand_id: self._reject(cid)),
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

    # ----- 编辑对话框字段（候选 / 记忆共用） -----
    def _build_edit_fields(self, initial: Dict[str, object]):
        content_field = ft.TextField(
            value=str(initial.get("content") or ""),
            label="内容",
            hint_text="助手该记住的事实陈述",
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
            value=str(initial.get("type", "fact")),
            options=[ft.dropdown.Option(key=k, text=TYPE_LABEL[k]) for k in _CANDIDATE_TYPES],
            text_size=t.CAPTION,
            border=ft.InputBorder.OUTLINE,
            border_color=c.BORDER,
            color=c.TEXT_PRIMARY,
        )
        importance_dd = ft.Dropdown(
            label="重要性",
            value=str(int(initial.get("importance", 3) or 3)),
            width=120,
            options=[ft.dropdown.Option(key=str(i), text=str(i)) for i in range(1, 11)],
            text_size=t.CAPTION,
            border=ft.InputBorder.OUTLINE,
            border_color=c.BORDER,
            color=c.TEXT_PRIMARY,
        )
        confidence_dd = ft.Dropdown(
            label="置信度",
            value=str(int(initial.get("confidence", 3) or 3)),
            width=120,
            options=[ft.dropdown.Option(key=str(i), text=str(i)) for i in range(1, 6)],
            text_size=t.CAPTION,
            border=ft.InputBorder.OUTLINE,
            border_color=c.BORDER,
            color=c.TEXT_PRIMARY,
        )
        return content_field, type_dd, importance_dd, confidence_dd

    # ----- 用户操作：候选（M3.2） -----
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

    def _open_modify_candidate(self, cand: Dict[str, object]) -> None:
        """M3.2 修改：人编辑 AI 的草稿；保存后仍 pending，由人决定确认或拒绝。"""
        if self.memory is None or self.page is None:
            return
        cand_id = int(cand["id"])
        content_field, type_dd, importance_dd, confidence_dd = self._build_edit_fields(cand)

        def _submit(_ev: ft.ControlEvent) -> None:
            text = (content_field.value or "").strip()
            if not text:
                content_field.error_text = "说点什么再让助手记。"
                content_field.update()
                return
            try:
                ok = self.memory.update_candidate(
                    cand_id,
                    type=str(type_dd.value or "fact"),
                    content=text,
                    importance=int(importance_dd.value or 3),
                    confidence=int(confidence_dd.value or 3),
                )
            except Exception as e:  # noqa: BLE001
                content_field.error_text = f"修改失败：{e}"
                content_field.update()
                return
            if not ok:
                content_field.error_text = "这条与已存在的记忆重复，换个说法试试。"
                content_field.update()
                return
            dialog.open = False
            self.page.update()
            self.refresh()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("修改助手的提议", size=t.BODY, weight=ft.FontWeight.BOLD),
            content=ft.Column(
                [content_field, ft.Container(height=sp.SM), type_dd, ft.Container(height=sp.XS),
                 ft.Row([importance_dd, confidence_dd], spacing=sp.SM)],
                spacing=sp.XS, tight=True, width=340,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self._close_dialog(dialog)),
                ft.FilledButton("保存（仍待确认）", on_click=_submit),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)

    # ----- 用户操作：已确认记忆（M3.3，人拥有最终控制权） -----
    def _forget(self, mem_id: int) -> None:
        if self.memory is None:
            return
        try:
            self.memory.forget_id(mem_id)
        except Exception as e:  # noqa: BLE001
            print(f"[memory-panel] 遗忘失败: {e}")
        self.refresh()

    def _open_modify_memory(self, mem: Dict[str, object]) -> None:
        """人修改一条已确认记忆（人拥有最终控制权）。"""
        if self.memory is None or self.page is None:
            return
        mem_id = int(mem["id"])
        content_field, type_dd, importance_dd, confidence_dd = self._build_edit_fields(mem)

        def _submit(_ev: ft.ControlEvent) -> None:
            text = (content_field.value or "").strip()
            if not text:
                content_field.error_text = "记忆内容不能为空。"
                content_field.update()
                return
            try:
                self.memory.edit_memory(
                    mem_id,
                    type=str(type_dd.value or "fact"),
                    content=text,
                    importance=int(importance_dd.value or 3),
                    confidence=int(confidence_dd.value or 3),
                )
            except Exception as e:  # noqa: BLE001
                content_field.error_text = f"修改失败：{e}"
                content_field.update()
                return
            dialog.open = False
            self.page.update()
            self.refresh()

        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("修改这条记忆", size=t.BODY, weight=ft.FontWeight.BOLD),
            content=ft.Column(
                [content_field, ft.Container(height=sp.SM), type_dd, ft.Container(height=sp.XS),
                 ft.Row([importance_dd, confidence_dd], spacing=sp.SM)],
                spacing=sp.XS, tight=True, width=340,
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda _: self._close_dialog(dialog)),
                ft.FilledButton("保存", on_click=_submit),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self.page.show_dialog(dialog)

    # ----- 整理候选（可选，仅 extractor 可用时显示） -----
    def _on_extract_click(self, _e: ft.ControlEvent) -> None:
        if self._on_extract is None or self.page is None:
            return
        if self._extract_btn is not None:
            self._extract_btn.disabled = True
            self._extract_btn.update()
        # 抽取走后台线程（会调 LLM），避免阻塞 UI
        self.page.run_thread(self._extract_worker)

    def _extract_worker(self) -> None:
        try:
            n = self._on_extract() if self._on_extract is not None else 0
        except Exception:  # noqa: BLE001 - 抽取失败只降级，绝不崩 UI
            n = -1

        async def _apply() -> None:
            if self._extract_btn is not None:
                self._extract_btn.disabled = False
                self._extract_btn.update()
            self.refresh()
            if self.page is not None:
                if n > 0:
                    msg = f"助手整理了 {n} 条候选，等你确认"
                elif n == 0:
                    msg = "最近没有可整理的新内容"
                else:
                    msg = "整理失败，请稍后再试"
                try:
                    self.page.show_dialog(ft.SnackBar(content=ft.Text(msg)))
                except Exception:  # noqa: BLE001
                    pass

        try:
            self.page.run_task(_apply)
        except Exception:  # noqa: BLE001 - 退化：直接刷新（主线程/测试场景）
            if self._extract_btn is not None:
                self._extract_btn.disabled = False
            self.refresh()

    # ----- 本轮检索高亮（由 Agent.on_retrieval 经 app 调度） -----
    def set_active_memories(self, hits) -> None:
        """高亮本轮助手想起的记忆。传入空列表即清除高亮。"""
        self._active_ids = {int(h.id) for h in hits}
        self.refresh()

    # ----- 新增记忆对话框（人主动告诉助手，非 AI 提议） -----
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
            options=[ft.dropdown.Option(key=k, text=TYPE_LABEL[k]) for k in _CANDIDATE_TYPES],
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
        self.page.show_dialog(dialog)

    def _close_dialog(self, dialog: ft.AlertDialog) -> None:
        dialog.open = False
        if self.page is not None:
            self.page.update()
