"""Agent 编排（Phase 1 最小闭环）。

Responsibility：人格加载 → 提示词拼装 → 调 LLM 流式 → 维护短期记忆窗口。
本阶段无持久化、无记忆抽取、无 Emotion 大系统——先钉死"助手人格稳定"。

阶段事件（on_phase）：在流式回复前发出 retrieving / reasoning 阶段，
供 UI 展示 RAG 检索过程。RAG 未接入前用模拟序列（短暂延迟）保证视觉完整；
Phase 2 接真检索后，自然替换为真实阶段回调，UI 无需改动。
"""
from __future__ import annotations

import time
from typing import Callable, Dict, Iterator, List, Optional

from .llm_client import LLMClient
from .persona import Persona
from .prompt_builder import PromptBuilder

# 阶段事件类型
PhaseCallback = Callable[[str], None]


class Agent:
    def __init__(
        self,
        persona: Persona,
        llm: LLMClient,
        history_limit: int = 20,
        on_phase: Optional[PhaseCallback] = None,
    ) -> None:
        self.persona = persona
        self.llm = llm
        self.prompt_builder = PromptBuilder(persona)
        self.system_prompt = self.prompt_builder.build_system()
        self.history_limit = history_limit
        self._on_phase = on_phase
        self._history: List[Dict[str, str]] = []  # 短期记忆（仅当前进程生命周期）

    def reply(self, user_text: str) -> Iterator[str]:
        """流式产出助手的回复文本。"""
        self._history.append({"role": "user", "content": user_text})
        window = self._history[-(self.history_limit * 2) :]

        # 阶段事件：检索 → 推理（RAG 未接入前为模拟序列，UI 可视化用）
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
            # 限制短期记忆总量，避免长会话无限增长（窗口之外不再保留）
            limit = self.history_limit * 2
            if len(self._history) > limit:
                self._history = self._history[-limit:]

    def reset(self) -> None:
        """清空短期记忆（新会话）。"""
        self._history.clear()
