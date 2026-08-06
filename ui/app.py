"""Flet 桌面 UI：助手三栏界面（浅色几何极简 + 本地舰桥基调）。

要点：
- 三栏布局：左「角色状态」/ 中「聊天区」/ 右「记忆档案」，传达存在感（Presence）。
- 用 page.run_thread 把 LLM 调用挪到后台，UI 不阻塞。
- 流式逐字更新气泡；思考态覆盖层外显 RAG 检索过程。
- 启动时显示空态欢迎区，不自动发送主动问候。
- API Key 缺失时显示明确错误，不把故障伪装成台词。
- 舰桥基调：极淡青色光晕呼吸作为背景动态元素（克制）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

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
from ui.components.memory_panel import MemoryPanel  # noqa: E402
from ui.components.persona_drawer import PersonaDrawer  # noqa: E402
from ui.components.persona_status import PersonaStatusPanel  # noqa: E402
from ui.design.avatar_provider import TextAvatarProvider  # noqa: E402
from ui.theme import ALIGN_CENTER, ALIGN_TOP_CENTER, c, layout, anim  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("assistant")


class AssistantApp:
    def __init__(self) -> None:
        self.page: ft.Page | None = None
        self.agent: Agent | None = None
        self.avatar_provider: TextAvatarProvider | None = None
        self.header: Header | None = None
        self.persona_status: PersonaStatusPanel | None = None
        self.chat_area: ChatArea | None = None
        self.input_bar: InputBar | None = None
        self.memory_panel: MemoryPanel | None = None
        self.middle: ft.Column | None = None
        self.drawer: PersonaDrawer | None = None
        self._glow: ft.Container | None = None
        self._session_start: float = 0.0

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

        self.avatar_provider = TextAvatarProvider(text="阿", bgcolor=c.PRIMARY, text_color=c.ON_PRIMARY)
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
            on_phase=self._on_phase,
        )

        self._setup_page(persona)
        self._build_ui(persona)
        self._start_ambience()

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

        # 右侧人格抽屉（点击 Header 人名展开）
        self.drawer = PersonaDrawer(persona, avatar_provider=self.avatar_provider)
        self.page.end_drawer = self.drawer

        # 三栏组件
        self.header = Header(persona, on_persona_click=self._open_drawer, avatar_provider=self.avatar_provider)
        self.persona_status = PersonaStatusPanel(persona, avatar_provider=self.avatar_provider)
        self.chat_area = ChatArea(persona, avatar_provider=self.avatar_provider)
        self.input_bar = InputBar(on_send=self._on_send)
        self.memory_panel = MemoryPanel(persona)

        self.middle = ft.Column(
            [self.chat_area, self.input_bar],
            expand=True,
            spacing=0,
        )

        body = ft.Row(
            [self.persona_status, self.middle, self.memory_panel],
            expand=True,
            spacing=layout.COLUMN_GAP,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )

        # 入场淡入（错峰），避免一次性闪现
        for ctrl in (self.header, self.persona_status, self.middle, self.memory_panel):
            ctrl.opacity = 0
            ctrl.animate_opacity = ft.Animation(anim.NORMAL, anim.EASE_OUT)

        # 舰桥基调背景：极淡青色光晕（呼吸）
        self._glow = ft.Container(
            width=560,
            height=560,
            border_radius=ft.BorderRadius.only(
                top_left=560, top_right=560, bottom_left=560, bottom_right=560
            ),
            bgcolor=c.BRIDGE_ACCENT,
            opacity=0.10,
        )
        bg = ft.Container(
            content=self._glow,
            alignment=ALIGN_TOP_CENTER,
            bgcolor=c.BG,
            expand=True,
        )
        root = ft.Stack([bg, ft.Column([self.header, body], expand=True)], expand=True)

        self.page.add(root)

        # 错峰入场
        assert self.page is not None
        self.page.run_task(self._reveal_all)

    async def _reveal_all(self) -> None:
        await asyncio.sleep(0)
        for ctrl, delay in (
            (self.header, 0),
            (self.persona_status, anim.STAGGER),
            (self.middle, anim.STAGGER * 2),
            (self.memory_panel, anim.STAGGER * 3),
        ):
            if ctrl is None:
                continue
            await asyncio.sleep(delay / 1000)
            ctrl.opacity = 1
            ctrl.update()

    def _start_ambience(self) -> None:
        """启动舰桥氛围：背景光晕呼吸 + 今日陪伴计时。"""
        assert self.page is not None
        self._session_start = time.monotonic()
        self.page.run_task(self._breathe)
        self.page.run_task(self._tick_companionship)

    async def _breathe(self) -> None:
        try:
            while True:
                if self._glow is not None:
                    self._glow.opacity = 0.16
                    self._glow.update()
                await asyncio.sleep(anim.BREATH_MS / 1000)
                if self._glow is not None:
                    self._glow.opacity = 0.06
                    self._glow.update()
                await asyncio.sleep(anim.BREATH_MS / 1000)
        except asyncio.CancelledError:
            return

    async def _tick_companionship(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                mins = int((time.monotonic() - self._session_start) / 60)
                if self.persona_status is not None:
                    self.persona_status.set_companionship_minutes(mins)
        except asyncio.CancelledError:
            return

    def _open_drawer(self) -> None:
        if self.drawer is not None and self.page is not None:
            self.drawer.open = True
            self.page.update()

    def _on_phase(self, phase: str) -> None:
        """由 Agent 阶段事件驱动思考态检索进度（RAG 可视化）。"""
        if self.chat_area is not None:
            self.chat_area.set_phase(phase)

    def _on_send(self, text: str) -> None:
        assert self.chat_area is not None and self.input_bar is not None and self.header is not None
        self.chat_area.add_user(text)
        self.input_bar.set_loading(True)
        self.header.set_thinking(True)
        if self.persona_status is not None:
            self.persona_status.set_state("thinking")
            self.persona_status.set_companionship_minutes(
                int((time.monotonic() - self._session_start) / 60)
            )
        assert self.agent is not None and self.page is not None
        self.page.run_thread(self._worker, text)

    def _worker(self, text: str) -> None:
        assert self.agent is not None and self.chat_area is not None
        assert self.input_bar is not None and self.header is not None and self.page is not None
        try:
            text_control = self.chat_area.start_assistant()
            started = False
            for delta in self.agent.reply(text):
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
            if self.persona_status is not None:
                self.persona_status.set_state("calm")
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
