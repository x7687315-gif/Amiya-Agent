"""Agent 编排层。

Responsibility：人格加载 → 提示词拼装 → 调 LLM 流式 → 维护短期记忆窗口
→（Phase 2-A 起）把每轮对话交给 MemoryManager 落盘。

分层：Agent Core → MemoryManager → MemoryStore(SQLite)。
本层**不认识 SQL、不认识 store**，只依赖中间层的 add_turn / recent_turns。

阶段事件（on_phase）：在流式回复前发出 retrieving / reasoning 阶段，
供 UI 展示 RAG 检索过程。retrieving 阶段由真实的 MemoryManager.retrieve()
驱动——UI 无需改动。

Prompt 记忆注入（Step 2.5）：检索命中经 PromptBuilder 定界注入系统提示词，
让助手「想得起」长期记忆。LLM 自动抽取（Step 2.7 M2.1）已**接线**但默认关闭
（EXTRACT_AUTO=False）——只在 reply() 末尾挂一个受守卫的钩子，绝不自动触发，
须由人或「🧹 整理记忆」显式发起；抽取只写 memory_candidate 闸门，不直写正表。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Dict, Iterator, List, Optional

from .llm_client import LLMClient
from .persona import Persona, PersonaManager
from .prompt_builder import PromptBuilder

if TYPE_CHECKING:  # 仅类型标注，运行期不强制依赖记忆/知识包
    from .knowledge.manager import KnowledgeManager
    from .memory.extraction_engine import ExtractionEngine
    from .memory.manager import MemoryManager, RetrievalHit

log = logging.getLogger(__name__)

# 阶段事件类型
PhaseCallback = Callable[[str], None]


class Agent:
    def __init__(
        self,
        persona: Persona,
        llm: LLMClient,
        history_limit: int = 20,
        on_phase: Optional[PhaseCallback] = None,
        memory: "Optional[MemoryManager]" = None,
        restore_history: bool = True,
        on_retrieval: "Optional[Callable[[List[RetrievalHit]], None]]" = None,
        memory_top_k: int = 5,
        knowledge: "Optional[KnowledgeManager]" = None,
        knowledge_top_k: int = 4,
        *,
        extractor: "Optional[ExtractionEngine]" = None,
        extract_auto: bool = False,
        default_extract_window: int = 10,
        manual_extract_window: int = 20,
    ) -> None:
        """
        memory: 记忆中间层。为 None 时行为与 Phase 1 完全一致（纯内存、不落盘），
                因此现有调用方与测试无需改动。
        restore_history: 冷启动时是否从库里回填短期窗口（"记得住"的最小体现）。
        on_retrieval: 检索命中回调（Step 2.6 的 UI 钩子），把命中项交给记忆面板等。
        memory_top_k: 每轮检索返回并注入提示词的最相关记忆条数。
        knowledge: 角色知识库（Knowledge RAG）。为 None 时不做知识检索，
                   与 memory 完全独立——知识是"设定"，记忆是"经历"，互不混入。
        knowledge_top_k: 每轮注入提示词的最相关知识片段条数。
        extractor: 记忆抽取引擎（可选）。为 None 时 Agent 行为与 M1 完全一致，
                   现有调用方无需改动；由 Composition Root 注入，业务层不创建它。
        extract_auto: 是否每轮自动抽取候选。默认 False——钩子存在但 dormant，
                     零 LLM 调用、零候选写入；手动整理不受影响。
        default_extract_window / manual_extract_window: 自动 / 手动抽取的上下文轮数。
        """
        self.persona = persona
        self.llm = llm
        self.prompt_builder = PromptBuilder(persona)
        self.system_prompt = self.prompt_builder.build_system()
        self.history_limit = history_limit
        self._on_phase = on_phase
        self._memory = memory
        # Persona 子系统门面：聚合固定身份 + 行为准则 + 动态关系（来自 persona_state）
        self._persona_manager = PersonaManager(persona, memory=self._memory)
        self._on_retrieval = on_retrieval
        self._memory_top_k = memory_top_k
        self._knowledge = knowledge
        self._knowledge_top_k = knowledge_top_k
        # 记忆抽取（Step 2.7 M2.1）：可选依赖，默认不触发
        self._extractor = extractor
        self._extract_auto = extract_auto
        self._default_extract_window = default_extract_window
        self._manual_extract_window = manual_extract_window
        self._history: List[Dict[str, str]] = []  # 短期窗口（喂给 LLM 的上下文）
        self.memory_degraded = False  # 记忆写入是否发生过失败（UI 可据此提示）
        if memory is not None and restore_history:
            self._restore_history()

    # ----- 记忆闭环 -----
    def _restore_history(self) -> None:
        """从库里回填最近若干条对话，让重启后仍接得上上次的话题。

        失败绝不能阻断启动——大不了从空白开始聊。
        """
        mem = self._memory
        if mem is None:  # 防御：调用点已判空，这里不用 assert（-O 会被剥掉）
            return
        try:
            turns = mem.recent_turns(limit=self.history_limit * 2)
        except Exception as e:  # noqa: BLE001 - 存储层任何异常都只降级不致命
            log.warning("恢复历史对话失败，将以空白上下文启动：%s", e)
            self.memory_degraded = True
            return
        self._history = [
            {"role": t["role"], "content": t["content"]}
            for t in turns
            if t.get("role") in ("user", "assistant") and t.get("content")
        ]

    def _remember(self, role: str, content: str) -> None:
        """落盘一条消息。记忆故障只降级、不打断对话。"""
        if self._memory is None:
            return
        try:
            self._memory.add_turn(role, content)
        except Exception as e:  # noqa: BLE001 - 聊天可用性优先于记录完整性
            log.warning("记忆写入失败（role=%s）：%s", role, e)
            self.memory_degraded = True

    def _trim_history(self) -> None:
        limit = self.history_limit * 2
        if len(self._history) > limit:
            self._history = self._history[-limit:]

    # ----- 记忆抽取（Step 2.7 M2.1） -----
    def _maybe_extract(self) -> None:
        """自动抽取钩子：仅在 EXTRACT_AUTO=True 且注入了 extractor 时触发。

        守卫位于 llm.chat 调用之前——False 时直接返回，零 LLM 调用、零候选写入，
        对现有行为零影响。
        """
        if not self._extract_auto or self._extractor is None:
            return
        self._run_extraction(self._default_extract_window)

    def extract_now(self, *, window_turns: "Optional[int]" = None) -> List[int]:
        """手动整理：立即把「自上次书签以来的新对话」抽成候选送进闸门。

        不受 EXTRACT_AUTO 约束（仅受 extractor 是否为 None 约束）——落实「手动整理优先」。
        供未来 UI「🧹 整理记忆」按钮调用。返回新入队的候选 id 列表（无引擎时 []）。
        """
        if self._extractor is None:
            return []
        return self._run_extraction(window_turns or self._manual_extract_window)

    def _run_extraction(self, window_turns: int) -> List[int]:
        """抽取核心（自动/手动共用）：取书签后新消息 → extract → 推进书签。

        任何异常都静默降级为返回 []，绝不崩对话。书签无论是否产出候选都会推进，
        避免下一轮重复处理同一批消息（last_extract_msg_id 存于 persona_state）。
        """
        if self._memory is None or self._extractor is None:
            return []
        try:
            bookmark = int(self._memory.state().get("last_extract_msg_id") or 0)
            turns = self._memory.messages_since(bookmark, limit=window_turns * 2)
        except Exception as e:  # noqa: BLE001
            log.warning("读取抽取窗口失败（已忽略）：%s", e)
            return []
        if not turns:
            return []
        # 只把中性 role/content 喂给抽取引擎，绝不混入助手 system prompt（三柱隔离）
        window = [{"role": t["role"], "content": t["content"]} for t in turns]
        try:
            ids = self._extractor.extract(window)
        except Exception:  # noqa: BLE001 - 抽取失败绝不崩对话
            log.warning("记忆自动抽取失败（已忽略）")
            ids = []
        # 推进书签到本批最新消息 id（与是否产出候选无关，保证幂等）
        try:
            self._memory.save_state(
                last_extract_msg_id=max(int(t["id"]) for t in turns)
            )
        except Exception as e:  # noqa: BLE001
            log.warning("抽取书签更新失败（已忽略）：%s", e)
        return ids

    # ----- 主循环 -----
    def reply(self, user_text: str) -> Iterator[str]:
        """流式产出助手的回复文本。"""
        self._history.append({"role": "user", "content": user_text})
        # 先落盘再请求 LLM：即使网络失败，用户说过的话也不会丢
        self._remember("user", user_text)
        window = self._history[-(self.history_limit * 2) :]

        # 真实检索阶段（Step 2.4 接入）：用用户本轮的话去翻长期记忆
        hits: List[RetrievalHit] = []
        if self._memory is not None:
            if self._on_phase is not None:
                self._on_phase("retrieving")
            try:
                hits = self._memory.retrieve(user_text, top_k=self._memory_top_k)
            except Exception as e:  # noqa: BLE001 - 检索失败只降级，不阻断对话
                log.warning("记忆检索失败：%s", e)
        if self._on_phase is not None:
            self._on_phase("reasoning")

        # 把检索到的记忆注入系统提示词（定界包裹防注入，不含分数）
        # 经 MemoryManager.format_block 渲染——编排层不直接依赖 retrieval 子层
        memory_block = self._memory.format_block(hits) if self._memory is not None else ""

        # 角色知识检索（Knowledge RAG）：与记忆并列、独立定界、互不混入。
        # 失败只降级——大不了这一轮不引用设定，绝不让对话崩掉。
        knowledge_block = ""
        if self._knowledge is not None:
            try:
                khits = self._knowledge.retrieve(user_text, top_k=self._knowledge_top_k)
            except Exception as e:  # noqa: BLE001
                log.warning("知识检索失败：%s", e)
                khits = []
            if khits:
                knowledge_block = self._knowledge.render_block(khits)

        # 人格先于上下文：身份 → 核心价值观 → 知识 → 记忆 → 关系 → 情绪 seam → 行为 → 语言
        relationship_block = self._persona_manager.relationship_block()
        behavior_block = self._persona_manager.behavior_block()
        system_prompt = self.prompt_builder.build_system(
            memory_block=memory_block,
            knowledge_block=knowledge_block,
            relationship_block=relationship_block,
            behavior_block=behavior_block,
            emotion_block=None,  # Emotion seam（本轮不接入真实模型）
        )

        # on_retrieval 回调：把命中项交给 2.6 的 UI（thinking overlay / 记忆面板）
        if self._on_retrieval is not None and hits:
            try:
                self._on_retrieval(hits)
            except Exception as e:  # noqa: BLE001
                log.warning("on_retrieval 回调异常：%s", e)

        full: str = ""
        for delta in self.llm.stream_chat(system_prompt, window):
            full += delta
            yield delta
        if full.strip():
            self._history.append({"role": "assistant", "content": full})
            self._remember("assistant", full)
            # 限制短期记忆总量，避免长会话无限增长（窗口之外不再保留）
            self._trim_history()
            # 记忆抽取钩子：EXTRACT_AUTO=False 时直接返回（零副作用），True 时才触发
            self._maybe_extract()

    def reset(self) -> None:
        """开启新会话：清空短期窗口并轮换 session_id，**绝不删库**。"""
        self._history.clear()
        if self._memory is not None:
            try:
                self._memory.new_session()
            except Exception as e:  # noqa: BLE001
                log.warning("轮换会话失败：%s", e)
                self.memory_degraded = True
