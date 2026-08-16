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
from typing import Callable, Optional

# 允许 `python ui/app.py` 直接运行（把项目根加入 path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import flet as ft  # noqa: E402

from config import ConfigError, load_settings  # noqa: E402
from core.agent import Agent  # noqa: E402
from core.llm_client import DeepSeekLLMClient  # noqa: E402
from core.memory import (  # noqa: E402
    ExtractionEngine,
    MemoryExtractor,
    MemoryManager,
    SQLiteMemoryStore,
    get_embedder,
)
from core.knowledge import build_knowledge_manager  # noqa: E402
from core.persona import load_persona  # noqa: E402
from core.tts.service import TTSService  # noqa: E402
from core.tts.player import AudioPlayer  # noqa: E402
from ui.components.chat_area import ChatArea  # noqa: E402
from ui.components.header import Header  # noqa: E402
from ui.components.input_bar import InputBar  # noqa: E402
from ui.components.memory_panel import MemoryPanel  # noqa: E402
from ui.components.persona_drawer import PersonaDrawer  # noqa: E402
from ui.components.persona_status import PersonaStatusPanel  # noqa: E402
from ui.components.speaker import MuteState  # noqa: E402
from ui.design.avatar_provider import ImageAvatarProvider, TextAvatarProvider  # noqa: E402
from ui.theme import ALIGN_CENTER, ALIGN_TOP_CENTER, c, layout, anim  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("assistant")


class AssistantApp:
    def __init__(self) -> None:
        self.page: ft.Page | None = None
        self.agent: Agent | None = None
        self.avatar_provider: "ImageAvatarProvider | TextAvatarProvider | None" = None
        self.header: Header | None = None
        self.persona_status: PersonaStatusPanel | None = None
        self.chat_area: ChatArea | None = None
        self.input_bar: InputBar | None = None
        self.memory_panel: MemoryPanel | None = None
        self.middle: ft.Column | None = None
        self.drawer: PersonaDrawer | None = None
        self._glow: ft.Container | None = None
        self._session_start: float = 0.0
        # 语音链路默认值：保证 run() 之前访问这些属性也不崩（测试可注入替换）。
        # tts 默认 None——未 run 就点朗读属异常路径，静默跳过而非 AttributeError。
        self.mute_state = MuteState()
        self.player = AudioPlayer()
        self.tts: TTSService | None = None
        self._memory = None
        self._extract_callback = None

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

        self.avatar_provider = ImageAvatarProvider(
            folder=os.path.join(os.path.dirname(__file__), "..", "resources", "avatar"),
            fallback=TextAvatarProvider(text="阿", bgcolor=c.PRIMARY, text_color=c.ON_PRIMARY),
        )
        # 记忆系统：启用时才建（MEMORY_ENABLED=1）。失败不致命——助手退回纯内存。
        memory = None
        if settings.memory_enabled:
            try:
                store = SQLiteMemoryStore(settings.memory_db_path)
                embedder = get_embedder(
                    backend=settings.embedding_backend,
                    model_name=settings.embedding_model,
                    device=settings.embedding_device,
                )
                memory = MemoryManager(store, embedder=embedder)
                # 启动时为存量 / 换模型后失效的记忆补向量（空库直接返回 0，不触发模型加载）
                memory.reindex()
            except Exception:  # noqa: BLE001
                logger.exception("记忆系统初始化失败，助手将以纯内存模式运行")
                memory = None

        # 角色知识库（Knowledge RAG）：knowledge/ 目录存在才启用，失败不致命。
        # 与记忆系统完全独立——知识是"设定"，记忆是"经历"，互不混入。
        knowledge = None
        try:
            knowledge = build_knowledge_manager(
                settings.knowledge_dir,
                backend=settings.embedding_backend,
                model_name=settings.embedding_model,
                device=settings.embedding_device,
                top_k=settings.knowledge_top_k,
            )
        except Exception:  # noqa: BLE001
            logger.exception("知识库初始化失败，助手将以无知识库模式运行")
            knowledge = None

        # LLM 客户端：Agent 回复与记忆抽取共用同一实例（抽取走独立提示词）。
        llm = DeepSeekLLMClient(
            api_key=settings.api_key,
            base_url=settings.base_url,
            model=settings.model,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            timeout=settings.timeout,
        )
        # 记忆抽取引擎（Step 2.7 M2.1）：保持依赖注入——在此 Composition Root 构造，
        # 业务层（Agent）不创建它。EXTRACT_AUTO 默认 False，钩子 dormant，零副作用。
        extractor = None
        if memory is not None:
            try:
                extractor = ExtractionEngine(MemoryExtractor(memory), llm)
            except Exception:  # noqa: BLE001
                logger.exception("抽取引擎初始化失败，自动记忆整理将不可用")
                extractor = None

        self.agent = Agent(
            persona=persona,
            llm=llm,
            history_limit=settings.history_limit,
            on_phase=self._on_phase,
            memory=memory,
            on_retrieval=self._on_retrieval,
            memory_top_k=settings.memory_top_k,
            knowledge=knowledge,
            knowledge_top_k=settings.knowledge_top_k,
            extractor=extractor,
            extract_auto=settings.extract_auto,
            default_extract_window=settings.default_extract_window,
            manual_extract_window=settings.manual_extract_window,
        )

        # 记忆整理回调（M3）：仅当抽取引擎可用时，面板「让助手整理候选」按钮才生效。
        # 点击后后台线程调 Agent.extract_now（AI 只提议 candidate），人再走 M3.2 拍板。
        self._extract_callback = self._make_extract_callback()

        # M3 语音朗读链路：与 Agent / LLM 完全解耦，失败降级不致命。
        # - MuteState：全局语音开关（ observable，默认开）。
        # - AudioPlayer：本地 wav 播放（winsound 后端，零依赖）。
        # - TTSService：薄 HTTP 客户端，把文本送去已运行的 GPT-SoVITS API。
        self.mute_state = MuteState()
        self.player = AudioPlayer()
        self.tts = TTSService()  # 默认加载 assistant 语音档案

        # 将记忆系统生命周期延长到实例属性，供 _build_ui 构造 MemoryPanel 使用
        self._memory = memory

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
        self.header = Header(
            persona,
            on_persona_click=self._open_drawer,
            avatar_provider=self.avatar_provider,
            mute_state=self.mute_state,
        )
        self.persona_status = PersonaStatusPanel(persona, avatar_provider=self.avatar_provider)
        self.chat_area = ChatArea(
            persona,
            avatar_provider=self.avatar_provider,
            on_speak=self._speak_async,
            mute_state=self.mute_state,
        )
        self.input_bar = InputBar(on_send=self._on_send)
        self.memory_panel = MemoryPanel(persona, memory=self._memory, on_extract=self._extract_callback)

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
        # InputBar 在 middle 内部、自身初始 opacity=0，需单独 reveal（否则底部输入框永久透明）
        if self.input_bar is not None:
            self.input_bar.reveal()

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

    def _make_extract_callback(self) -> "Optional[Callable[[], int]]":
        """构造「让助手整理候选」回调（仅抽取引擎可用时非 None）。

        返回 callable：调用即在后台线程跑 Agent.extract_now，返回新入队候选数；
        抽取引擎缺失 / Agent 未就绪时返回 None，面板即不显示该按钮。
        """
        agent = self.agent
        if agent is None or not agent.can_extract:
            return None

        def _run() -> int:
            try:
                return len(agent.extract_now() or [])
            except Exception:  # noqa: BLE001 - 抽取失败只降级，绝不崩 UI
                return -1

        return _run

    def _speak_async(self, text: str) -> None:
        """在后台线程朗读（TTS 网络往返约 1-2s，绝不在主线程阻塞 UI）。

        由气泡 🔊 按钮的 on_click 调用，文本固定为「该气泡自己的台词」。
        """
        assert self.page is not None
        self.page.run_thread(self._speak, text)

    def _speak(self, text: str) -> None:
        """朗读一句台词：TTS 合成 → 本地播放。失败只降级，不抛、不拖垮 UI。

        原则（M3）：语音失败 ≠ Agent 失败。合成或播放失败都静默跳过本条朗读。
        整个链路（网络合成 + 本地播放）都在后台线程跑（_speak_async 经 run_thread
        调度），不阻塞 UI；AudioPlayer 内部用唯一工作线程按 FIFO 串行播放，
        连点多个气泡不会互相截断。
        """
        if not text or not text.strip():
            return  # 空文本 / 纯空白：没有可朗读内容，跳过
        if self.tts is None:
            return  # run() 尚未完成：语音链路未就绪，静默跳过
        if self.mute_state is not None and self.mute_state.muted:
            return  # 全局静音：直接跳过，不发任何 TTS 请求
        logger.info("正在为文本请求助手语音：%s", text[:40])
        result = self.tts.synthesize(text=text, voice="assistant", text_lang="zh")
        if self.mute_state is not None and self.mute_state.muted:
            return  # 合成期间被静音：结果作废，不播（网络往返 1-2s，存在窗口期）
        if result.success and result.audio is not None and getattr(result.audio, "data", b""):
            if self.player.play(result.audio):
                logger.info("助手语音已排队播放")
            else:
                logger.warning("助手语音播放失败（winsound 不可用或音频无效）")
        else:
            logger.warning("助手语音合成失败，本条不朗读：%s", result.error)

    def _open_drawer(self) -> None:
        if self.drawer is not None and self.page is not None:
            self.drawer.open = True
            self.page.update()

    def _on_phase(self, phase: str) -> None:
        """由 Agent 阶段事件驱动思考态检索进度（RAG 可视化）。"""
        if self.chat_area is not None:
            self.chat_area.set_phase(phase)

    def _on_retrieval(self, hits) -> None:
        """检索命中回调：把本轮想起的记忆交给记忆面板高亮（清债 #2，占位钩子落地）。

        该回调运行在后台 worker 线程（Agent.reply 经 page.run_thread 执行），
        直接用 Flet 控件更新会跨线程操作 UI。这里通过 page.run_task 把更新
        调度回主事件循环，避免线程竞争。主线程场景下 run_task 同样安全。
        """
        panel = self.memory_panel
        if panel is None or self.page is None:
            return

        async def _apply() -> None:
            panel.set_active_memories(hits)

        try:
            self.page.run_task(_apply)
        except Exception:  # noqa: BLE001 - 退化：直接调用（主线程/测试场景）
            panel.set_active_memories(hits)

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
        except Exception:  # noqa: BLE001
            logger.exception("回复失败")
            # 异常细节只进日志，不进 UI（用户看到固定话术即可，见 config 哲学：
            # 既不把故障伪装成台词，也不把 stack 细节直接甩给用户）
            self.chat_area.add_error("通讯有些干扰，请稍后再试。")
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
