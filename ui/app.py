"""Flet 桌面 UI：助手聊天界面（Phase 1）。

要点：
- 用 page.run_thread 把 LLM 调用挪到后台，UI 不阻塞（解决旧版假死）。
- 流式逐字更新气泡。
- 启动时助手主动打招呼（欢迎语，非游戏原声）。
- API Key 缺失时显示明确错误，不把故障伪装成台词。
"""
from __future__ import annotations

import logging
import os
import sys

# 允许 `python ui/app.py` 直接运行（把项目根加入 path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft  # noqa: E402

from config import ConfigError, load_settings  # noqa: E402
from core.agent import Agent  # noqa: E402
from core.llm_client import DeepSeekLLMClient  # noqa: E402
from core.persona import load_persona  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("assistant")

ACCENT = "#8E7CC3"  # 助手紫
BG = "#15161D"
PANEL = "#1E1F2A"
BUBBLE_USER = "#2A2C3A"
BUBBLE_AI = "#25223A"
TEXT = "#E8E6F0"
MUTED = "#9A94BC"


class AssistantApp:
    def __init__(self) -> None:
        self.page: ft.Page | None = None
        self.chat_log: ft.ListView | None = None
        self.input_field: ft.TextField | None = None
        self.thinking_ref: ft.Control | None = None
        self.agent: Agent | None = None

    def run(self, page: ft.Page) -> None:
        self.page = page
        try:
            settings = load_settings()
        except ConfigError as e:
            self._fatal(str(e))
            return
        try:
            persona = load_persona()
        except Exception as e:  # noqa: BLE001
            logger.exception("人格加载失败")
            self._fatal(f"人格配置加载失败：{e}")
            return

        self.agent = Agent(
            persona=persona,
            llm=DeepSeekLLMClient(
                api_key=settings.api_key,
                base_url=settings.base_url,
                model=settings.model,
                temperature=settings.temperature,
                max_tokens=settings.max_tokens,
                timeout=settings.timeout,
            ),
            history_limit=settings.history_limit,
        )

        page.title = f"{persona.name} · 你的伙伴"
        page.bgcolor = BG
        page.window.width = 460
        page.window.height = 720
        page.window.min_width = 380
        page.window.min_height = 520
        page.padding = 0
        page.spacing = 0
        page.font_family = "Microsoft YaHei"

        self._build_ui(persona)
        self._add_assistant("用户，欢迎回来。今天过得还好吗？", animate=False)

    # ---------- UI 构建 ----------
    def _build_ui(self, persona) -> None:
        header = ft.Container(
            content=ft.Row(
                [
                    ft.CircleAvatar(
                        bgcolor=ACCENT,
                        radius=20,
                        content=ft.Text(persona.name[0], color="#fff", size=18, weight=ft.FontWeight.BOLD),
                    ),
                    ft.Column(
                        [
                            ft.Text(persona.name, color=TEXT, size=15, weight=ft.FontWeight.BOLD),
                            ft.Text(persona.title or "本地", color=MUTED, size=11),
                        ],
                        spacing=2,
                        expand=True,
                    ),
                ],
                spacing=12,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            bgcolor=PANEL,
            padding=ft.Padding.only(left=16, right=16, top=10, bottom=10),
            border=ft.Border.only(bottom=ft.BorderSide(width=1, color="#2A2C3A")),
        )

        self.chat_log = ft.ListView(
            controls=[],
            spacing=14,
            padding=ft.Padding.only(left=14, right=14, top=14, bottom=8),
            auto_scroll=True,
            expand=True,
        )

        self.input_field = ft.TextField(
            hint_text="和助手说点什么…",
            hint_style=ft.TextStyle(color=MUTED),
            text_style=ft.TextStyle(color=TEXT, size=14),
            bgcolor="#23252F",
            border=ft.InputBorder.NONE,
            border_radius=ft.BorderRadius.only(12, 12, 12, 12),
            content_padding=ft.Padding.only(left=14, right=14, top=10, bottom=10),
            cursor_color=ACCENT,
            multiline=True,
            min_lines=1,
            max_lines=4,
            expand=True,
            on_submit=self._on_send,
        )
        send_btn = ft.IconButton(
            icon=ft.icons.SEND_ROUNDED,
            icon_color="#fff",
            bgcolor=ACCENT,
            tooltip="发送",
            on_click=self._on_send,
            width=44,
            height=44,
        )
        input_row = ft.Container(
            content=ft.Row([self.input_field, send_btn], spacing=8, vertical_alignment=ft.CrossAxisAlignment.END),
            padding=ft.Padding.only(left=14, right=14, top=8, bottom=12),
            bgcolor=PANEL,
            border=ft.Border.only(top=ft.BorderSide(width=1, color="#2A2C3A")),
        )

        self.page.add(header, self.chat_log, input_row)

    # ---------- 交互 ----------
    def _on_send(self, _e) -> None:
        text = (self.input_field.value or "").strip()
        if not text:
            return
        self.input_field.value = ""
        self.page.update()
        self._add_user(text)
        self.page.run_thread(self._worker, text)

    def _worker(self, text: str) -> None:
        assert self.agent is not None and self.page is not None
        try:
            ai_text = ft.Text("", color=TEXT, size=14, selectable=True, line_height=1.5)
            started = False
            for _delta in self.agent.reply(text):
                if not started:
                    self._remove_thinking()
                    self.chat_log.controls.append(self._ai_row(ai_text))
                    started = True
                self.page.update()
            if not started:
                self._remove_thinking()
                self._add_assistant("（用户，我一时语塞了……）")
        except Exception as e:  # noqa: BLE001
            logger.exception("回复失败")
            self._remove_thinking()
            self._add_assistant(f"（用户，通讯有些干扰……{e}）")

    # ---------- 气泡 ----------
    def _add_user(self, text: str) -> None:
        self.chat_log.controls.append(
            ft.Row(
                [
                    ft.Container(expand=True),
                    ft.Container(
                        content=ft.Text(text, color=TEXT, size=14, line_height=1.5, selectable=True),
                        bgcolor=BUBBLE_USER,
                        padding=ft.Padding.only(left=12, right=12, top=9, bottom=9),
                        border_radius=ft.BorderRadius.only(14, 14, 4, 14),
                        margin=ft.Margin.only(left=48, right=4, top=0, bottom=0),
                    ),
                ],
                spacing=0,
            )
        )
        self.chat_log.controls.append(self._thinking())
        self.page.update()

    def _add_assistant(self, text: str, animate: bool = True) -> None:
        t = ft.Text(text, color=TEXT, size=14, line_height=1.5, selectable=True)
        self.chat_log.controls.append(self._ai_row(t))
        self.page.update()

    def _ai_row(self, text_control: ft.Text) -> ft.Control:
        return ft.Row(
            [
                ft.CircleAvatar(
                    bgcolor=ACCENT,
                    radius=16,
                    content=ft.Text("阿", color="#fff", size=13, weight=ft.FontWeight.BOLD),
                ),
                ft.Container(
                    content=text_control,
                    bgcolor=BUBBLE_AI,
                    padding=ft.Padding.only(left=12, right=12, top=9, bottom=9),
                    border_radius=ft.BorderRadius.only(4, 14, 14, 14),
                    margin=ft.Margin.only(left=4, right=48, top=0, bottom=0),
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.START,
        )

    def _thinking(self) -> ft.Control:
        self.thinking_ref = ft.Row(
            [
                ft.CircleAvatar(
                    bgcolor=ACCENT,
                    radius=16,
                    content=ft.Text("阿", color="#fff", size=13, weight=ft.FontWeight.BOLD),
                ),
                ft.Text("助手正在思考…", color=MUTED, size=12, italic=True),
            ],
            spacing=8,
        )
        return self.thinking_ref

    def _remove_thinking(self) -> None:
        if self.thinking_ref is not None and self.thinking_ref in self.chat_log.controls:
            self.chat_log.controls.remove(self.thinking_ref)
            self.thinking_ref = None
            self.page.update()

    def _fatal(self, msg: str) -> None:
        self.page.add(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.icons.ERROR_OUTLINE, color="#FF6B6B", size=40),
                        ft.Text("无法启动助手", color=TEXT, size=18, weight=ft.FontWeight.BOLD),
                        ft.Text(msg, color=MUTED, size=13, width=380),
                    ],
                    spacing=12,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=40,
                alignment=ft.alignment.center,
                expand=True,
            )
        )
        self.page.update()


def main(page: ft.Page) -> None:
    AssistantApp().run(page)


if __name__ == "__main__":
    ft.app(target=main)
