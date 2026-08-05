"""Flet 桌面 UI：助手聊天界面（浅色几何极简版）。

要点：
- 用 page.run_thread 把 LLM 调用挪到后台，UI 不阻塞。
- 流式逐字更新气泡。
- 启动时显示空态欢迎区，不自动发送主动问候。
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
from ui.components.chat_area import ChatArea  # noqa: E402
from ui.components.header import Header  # noqa: E402
from ui.components.input_bar import InputBar  # noqa: E402
from ui.components.persona_drawer import PersonaDrawer  # noqa: E402
from ui.theme import ALIGN_CENTER, c, layout  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("assistant")


class AssistantApp:
    def __init__(self) -> None:
        self.page: ft.Page | None = None
        self.agent: Agent | None = None
        self.header: Header | None = None
        self.chat_area: ChatArea | None = None
        self.input_bar: InputBar | None = None
        self.drawer: PersonaDrawer | None = None

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

        self._setup_page(persona)
        self._build_ui(persona)

    def _setup_page(self, persona) -> None:
        assert self.page is not None
        self.page.title = f"{persona.name} · 你的伙伴"
        self.page.bgcolor = c.BG
        self.page.window.width = layout.WINDOW_WIDTH
        self.page.window.height = layout.WINDOW_HEIGHT
        self.page.window.min_width = layout.WINDOW_MIN_WIDTH
        self.page.window.min_height = layout.WINDOW_MIN_HEIGHT
        self.page.padding = 0
        self.page.spacing = 0
        self.page.font_family = "Microsoft YaHei"

    def _build_ui(self, persona) -> None:
        assert self.page is not None
        self.drawer = PersonaDrawer(persona)
        self.page.end_drawer = self.drawer

        self.header = Header(persona, on_persona_click=self._open_drawer)
        self.chat_area = ChatArea(persona)
        self.input_bar = InputBar(on_send=self._on_send)

        self.page.add(self.header, self.chat_area, self.input_bar)

    def _open_drawer(self) -> None:
        if self.drawer is not None and self.page is not None:
            self.drawer.open = True
            self.page.update()

    def _on_send(self, text: str) -> None:
        assert self.chat_area is not None and self.input_bar is not None and self.header is not None
        self.chat_area.add_user(text)
        self.input_bar.set_loading(True)
        self.header.set_thinking(True)
        assert self.page is not None
        self.page.run_thread(self._worker, text)

    def _worker(self, text: str) -> None:
        assert self.agent is not None and self.chat_area is not None
        assert self.input_bar is not None and self.header is not None and self.page is not None
        try:
            text_control = self.chat_area.start_assistant()
            started = False
            for delta in self.agent.reply(text):
                if not started:
                    started = True
                self.chat_area.append_assistant_text(text_control, delta)
            if not started:
                self.chat_area.remove_thinking()
                self.chat_area.add_assistant("（用户，我一时语塞了……）")
        except Exception as e:  # noqa: BLE001
            logger.exception("回复失败")
            self.chat_area.add_error(f"通讯有些干扰，请稍后再试。{e}")
        finally:
            self.header.set_thinking(False)
            self.input_bar.set_loading(False)
            self.input_bar.focus()

    def _fatal(self, msg: str) -> None:
        assert self.page is not None
        self.page.add(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.ERROR_OUTLINE, color=c.ERROR, size=48),
                        ft.Text("无法启动助手", color=c.TEXT_PRIMARY, size=18, weight=ft.FontWeight.BOLD),
                        ft.Text(msg, color=c.TEXT_SECONDARY, size=13, width=380),
                    ],
                    spacing=16,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                padding=40,
                alignment=ALIGN_CENTER,
                expand=True,
            )
        )
        self.page.update()


def main(page: ft.Page) -> None:
    AssistantApp().run(page)


if __name__ == "__main__":
    ft.app(target=main)
