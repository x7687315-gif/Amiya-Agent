"""Agent 编排层。

Responsibility：人格加载 → 提示词拼装 → 调 LLM 流式 → 维护短期记忆窗口
→（Phase 2-A 起）把每轮对话交给 MemoryManager 落盘。

分层：Agent Core → MemoryManager → MemoryStore(SQLite)。
本层**不认识 SQL、不认识 store**，只依赖中间层的 add_turn / recent_turns。

阶段事件（on_phase）：在流式回复前发出 retrieving / reasoning 阶段，
供 UI 展示 RAG 检索过程。真检索（Step 2.4）接入前用模拟序列保证视觉完整，
届时自然替换为真实阶段回调，UI 无需改动。

Phase 2-A Step 2.2 边界：只做对话记录闭环。
记忆检索（2.4）、Prompt 记忆注入（2.5）、LLM 自动抽取（2.7）均不在本步。
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable, Dict, Iterator, List, Optional

from .llm_client import LLMClient
from .persona import Persona
from .prompt_builder import PromptBuilder

if TYPE_CHECKING:  # 仅类型标注，运行期不强制依赖记忆包
    from .memory.manager import MemoryManager

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
    ) -> None:
        """
        memory: 记忆中间层。为 None 时行为与 Phase 1 完全一致（纯内存、不落盘），
                因此现有调用方与测试无需改动。
        restore_history: 冷启动时是否从库里回填短期窗口（"记得住"的最小体现）。
        """
        self.persona = persona
        self.llm = llm
        self.prompt_builder = PromptBuilder(persona)
        self.system_prompt = self.prompt_builder.build_system()
        self.history_limit = history_limit
        self._on_phase = on_phase
        self._memory = memory
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

        # 阶段事件：检索 → 推理（真检索 Step 2.4 接入前为模拟序列，UI 可视化用）
        if self._on_phase is not None:
            self._on_phase("retrieving")
            time.sleep(0.45)
            self._on_phase("reasoning")
            time.sleep(0.45)

        full: str = ""
        for delta in self.llm.stream_chat(self.system_prompt, window):
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
