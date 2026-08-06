"""Agent 编排层。

Responsibility：人格加载 → 提示词拼装 → 调 LLM 流式 → 维护短期记忆窗口
→（Phase 2-A 起）把每轮对话交给 MemoryManager 落盘。

分层：Agent Core → MemoryManager → MemoryStore(SQLite)。
本层**不认识 SQL、不认识 store**，只依赖中间层的 add_turn / recent_turns。

阶段事件（on_phase）：在流式回复前发出 retrieving / reasoning 阶段，
供 UI 展示 RAG 检索过程。retrieving 阶段由真实的 MemoryManager.retrieve()
驱动——UI 无需改动。

Prompt 记忆注入（Step 2.5）：检索命中经 PromptBuilder 定界注入系统提示词，
让助手「想得起」长期记忆。LLM 自动抽取（2.7）仍不在本步。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Dict, Iterator, List, Optional

from .llm_client import LLMClient
from .persona import Persona
from .prompt_builder import PromptBuilder

if TYPE_CHECKING:  # 仅类型标注，运行期不强制依赖记忆包
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
    ) -> None:
        """
        memory: 记忆中间层。为 None 时行为与 Phase 1 完全一致（纯内存、不落盘），
                因此现有调用方与测试无需改动。
        restore_history: 冷启动时是否从库里回填短期窗口（"记得住"的最小体现）。
        on_retrieval: 检索命中回调（Step 2.6 的 UI 钩子），把命中项交给记忆面板等。
        memory_top_k: 每轮检索返回并注入提示词的最相关记忆条数。
        """
        self.persona = persona
        self.llm = llm
        self.prompt_builder = PromptBuilder(persona)
        self.system_prompt = self.prompt_builder.build_system()
        self.history_limit = history_limit
        self._on_phase = on_phase
        self._memory = memory
        self._on_retrieval = on_retrieval
        self._memory_top_k = memory_top_k
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
        system_prompt = self.prompt_builder.build_system(memory_block)

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

    def reset(self) -> None:
        """开启新会话：清空短期窗口并轮换 session_id，**绝不删库**。"""
        self._history.clear()
        if self._memory is not None:
            try:
                self._memory.new_session()
            except Exception as e:  # noqa: BLE001
                log.warning("轮换会话失败：%s", e)
                self.memory_degraded = True
