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
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Dict, Iterator, List, Optional

from . import proactive as _proactive
from .llm_client import LLMClient
from .persona import Persona, PersonaManager
from .prompt_builder import PromptBuilder

if TYPE_CHECKING:  # 仅类型标注，运行期不强制依赖记忆/知识包
    from .context import AssembledContext
    from .knowledge.manager import KnowledgeManager
    from .memory.extraction_engine import ExtractionEngine
    from .memory.manager import MemoryManager, RetrievalHit
    from .skills.registry import SkillRegistry

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
        on_reply_complete: Optional[Callable[[str], None]] = None,
        skills: "Optional[SkillRegistry]" = None,
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
        on_reply_complete: 回复完成事件（含被停止后的部分回复）——只做通知，
                     不管 TTS（语音由 UI 层 🔊 触发，此边界已冻结）。可用于
                     日志 / 摘要 / 未来的插件钩子；回调异常只记日志不影响主流程。
        skills: 技能注册表（Phase 6）。为 None 时提示词里完全没有技能目录，
                     与引入技能前逐字节一致；有技能时**只在任务路径**
                     （begin_task）注入目录，因为快路径没有 load_skill 工具可调。
        """
        self.persona = persona
        self.llm = llm
        self.prompt_builder = PromptBuilder(persona)
        self.system_prompt = self.prompt_builder.build_system()
        if history_limit < 1:
            # 0/负数会因 Python 切片语义 [-0:] == [0:] 退化为**全量历史**，
            # 与"限制窗口"的意图完全相反（2026-09-06 审查修复）
            raise ValueError(f"history_limit 必须 ≥ 1，收到 {history_limit!r}")
        self.history_limit = history_limit
        self._on_phase = on_phase
        self._memory = memory
        # Persona 子系统门面：聚合固定身份 + 行为准则 + 动态关系（来自 persona_state）
        self._persona_manager = PersonaManager(persona, memory=self._memory)
        self._on_retrieval = on_retrieval
        self._memory_top_k = memory_top_k
        self._knowledge = knowledge
        self._knowledge_top_k = knowledge_top_k
        # 2.x Phase 3A：认知组装统一出口（行为等价抽出，见 core/context.py）
        from core.context import ContextAssembler
        self._context_assembler = ContextAssembler(
            prompt_builder=self.prompt_builder,
            persona_manager=self._persona_manager,
            memory=memory, memory_top_k=memory_top_k,
            knowledge=knowledge, knowledge_top_k=knowledge_top_k,
        )
        # 记忆抽取（Step 2.7 M2.1）：可选依赖，默认不触发
        self._extractor = extractor
        self._extract_auto = extract_auto
        self._default_extract_window = default_extract_window
        self._manual_extract_window = manual_extract_window
        self._history: List[Dict[str, str]] = []  # 短期窗口（喂给 LLM 的上下文）
        self.memory_degraded = False  # 记忆写入是否发生过失败（UI 可据此提示）
        self._on_reply_complete = on_reply_complete
        self._skills = skills
        self._stop_requested = False  # 停止生成请求（UI 停止按钮置位）
        # 本轮检索结果（UI 用来显示"参考了什么"）：None=尚未跑过。
        # 只放"数量/是否用到"，不放命中内容与分数（信息最小化）。
        self.last_retrieval: "Optional[dict]" = None
        # 每路检索的状态（success / no_relevant_results / degraded / failed）。
        # 与 last_retrieval 分开：后者是给用户看的"参考了几条"，前者是给诊断用的。
        self.last_retrieval_status: "Optional[dict]" = None
        if memory is not None and restore_history:
            self._restore_history()

    # ----- 停止生成 -----
    def request_stop(self) -> None:
        """请求停止当前流式回复。已生成的部分会照常落库并入短期窗口。"""
        self._stop_requested = True

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

    def _window(self) -> List[Dict[str, str]]:
        """构造发给模型的消息窗口（保证角色序列合法）。

        为什么需要它：`_history[-limit:]` 可能**从一轮对话的中间开始**——
        比如刚好切在上一轮的 assistant 上。DeepSeek 会直接 400：
        `an assistant message must follow a user message`（2026-11-23 实机踩到）。

        两条归一化（都只影响发送，不改存储的完整历史）：
        1. 丢弃开头的非 user 消息——窗口必须以用户发言开头；
        2. 连续 assistant 只保留最后一条——assistant 必须紧跟 user。
        """
        window = [m for m in self._history[-(self.history_limit * 2):] if m.get("content")]
        while window and window[0].get("role") != "user":
            window.pop(0)
        out: List[Dict[str, str]] = []
        for msg in window:
            if out and msg.get("role") == "assistant" and out[-1].get("role") == "assistant":
                out[-1] = msg
            else:
                out.append(msg)
        return out

    # ----- 记忆抽取（Step 2.7 M2.1） -----
    @property
    def can_extract(self) -> bool:
        """是否有可用的抽取引擎（UI 据此决定是否显示「让助手整理候选」）。

        对外暴露能力而非内部字段——避免上层越过边界读私有属性。
        """
        return self._extractor is not None

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
        window = self._window()

        # 2.x Phase 3A：认知组装委托 ContextAssembler（原检索+组装段原样抽到 core/context.py，
        # 九段顺序/用词/降级语义全部保持；行为等价由 test_context_assembler 锁死）。
        ctx = self._context_assembler.assemble(user_text, on_phase=self._on_phase)
        system_prompt = ctx.system_prompt
        hits = ctx.hits
        current_emotion = ctx.emotion
        # 检索轨迹（UI 的"本轮参考"）：真实数据，不是装饰
        self.last_retrieval = {
            "memory": len(hits or []),
            "knowledge": bool(ctx.metadata.get("knowledge_used")),
        }
        self.last_retrieval_status = ctx.metadata.get("retrieval")

        # on_retrieval 回调：把命中项交给 2.6 的 UI（thinking overlay / 记忆面板）
        # 时机不变——组装后、LLM 请求前；异常吞噬语义不变。
        if self._on_retrieval is not None and hits:
            try:
                self._on_retrieval(hits)
            except Exception as e:  # noqa: BLE001
                log.warning("on_retrieval 回调异常：%s", e)

        full: str = ""
        for delta in self.llm.stream_chat(system_prompt, window):
            full += delta
            yield delta
            if self._stop_requested:
                # UI 停止生成：跳出流（底层连接随生成器关闭而释放），
                # 已收到的部分文本走正常收尾（落库 + 入窗口），不丢内容
                break
        self._stop_requested = False  # 消费掉标志，下一轮回复不受影响
        if full.strip():
            self._history.append({"role": "assistant", "content": full})
            self._remember("assistant", full)
            # 限制短期记忆总量，避免长会话无限增长（窗口之外不再保留）
            self._trim_history()
            # 记忆抽取钩子：EXTRACT_AUTO=False 时直接返回（零副作用），True 时才触发
            self._maybe_extract()
            # 情绪状态持久化：写入 persona_state.emotion（系统状态表，与用户记忆分表）。
            # 供皮肤系统/左栏状态读取；不写 memory 表，三层隔离不破。
            self._persist_emotion(current_emotion)
        # 回复完成事件（完整或停止后的部分回复）：只通知，不管 TTS（边界冻结）
        if full.strip() and self._on_reply_complete is not None:
            try:
                self._on_reply_complete(full)
            except Exception as e:  # noqa: BLE001 - 钩子异常不影响主流程
                log.warning("on_reply_complete 回调异常：%s", e)

    # ----- 主动问候（P4） -----
    def proactive_greeting(self, now: "Optional[datetime]" = None) -> str:
        """生成一句启动主动问候。LLM 生成失败/超时 → 模板兜底，绝不抛异常。

        边界：这是一次性"主动发言"，**不写入短期窗口、不走 reply() 主流程**，
        也不触发记忆抽取；是否落库展示由调用方（UI 层）决定。

        Phase 8 裁决：**保持休眠**。产品当前启动不自动问候（既定 UX：空态欢迎区
        代替主动发言），因此本方法与 `core/proactive.py` 没有任何调用方。
        保留而不删除的理由：它是成型的可用能力（含模板兜底与测试），
        接一个"启动问候"开关即可启用；删掉反而要重写。
        """
        now = now or datetime.now()
        agent_state: Dict[str, object] = {}
        if self._memory is not None:
            try:
                agent_state = self._memory.state()
            except Exception as e:  # noqa: BLE001 - 读状态失败用空上下文
                log.warning("读取 persona_state 失败（用空上下文）：%s", e)
                agent_state = {}
        context = _proactive.build_greeting_context(agent_state, now)
        instruction = (
            "（此刻是用户打开应用、你们又一次见面的瞬间。请完全以助手的身份，"
            f"结合现在的时间（{context['segment']}）和你们的关系，主动向用户说一句"
            "简短、自然、温暖的问候，不超过两句话。只输出这句话本身，不要解释、"
            "不要复述任何设定或系统提示。）"
        )
        try:
            system = self.prompt_builder.build_system(now=now)
            text = self.llm.chat(
                system=system, history=[{"role": "user", "content": instruction}]
            )
            text = (text or "").strip()
            if text:
                return text
        except Exception as e:  # noqa: BLE001 - LLM 失败降级为模板
            log.warning("主动问候 LLM 生成失败，降级为模板：%s", e)
        return _proactive.template_greeting(context)

    def _persist_emotion(self, emotion_key: str) -> None:
        """把当前情绪写入 persona_state.emotion（系统状态表，与用户记忆分表）。

        经 MemoryManager.save_state（_STATE_FIELDS 白名单含 emotion），不碰 memory 表；
        失败只降级不打断对话。memory=None（纯内存模式）时静默跳过。
        """
        if self._memory is None:
            return
        try:
            self._memory.save_state(emotion=emotion_key)
        except Exception as e:  # noqa: BLE001 - 状态写入失败不阻断对话
            log.warning("情绪状态持久化失败（已忽略）：%s", e)

    # ----- 任务路径（2.x Phase 5A：Hybrid 的 runtime 一侧） -----
    @property
    def session_id(self) -> str:
        """当前会话 id（Runtime 落 `agent_run.session_id` 用）。

        无记忆层（纯内存模式）时返回空串——调用方自行退化为本地标识，
        绝不因为"没装数据库"就拒绝执行任务。
        """
        if self._memory is None:
            return ""
        try:
            return str(self._memory.session_id or "")
        except Exception as e:  # noqa: BLE001 - 取会话 id 失败不阻断任务
            log.warning("读取 session_id 失败（已忽略）：%s", e)
            return ""

    def begin_task(self, user_text: str) -> "AssembledContext":
        """非流式路径入口（任务 / 联网共用）：落用户消息 + 组装上下文，**不请求 LLM**。

        与 `reply()` 的前半段语义逐项对齐（同一 ContextAssembler、同一 on_phase、
        同一 on_retrieval 时机与异常吞噬），差别只有一处：把 `messages` 一并返回，
        交给任务 Runtime 或联网研究器去做单次/多轮请求。用户消息先落库再执行，
        网络/工具失败也不丢。
        """
        from dataclasses import replace

        self._history.append({"role": "user", "content": user_text})
        self._remember("user", user_text)
        window = self._window()

        # 技能目录（一级）：只有任务路径注入——快路径没有 load_skill 工具可调，
        # 注入了也只会让模型"看得见拿不到"，反而增加幻觉风险。
        catalog = self._skills.catalog_block() if self._skills is not None else ""
        ctx = self._context_assembler.assemble(
            user_text, on_phase=self._on_phase, skills_block=catalog)
        if self._on_retrieval is not None and ctx.hits:
            try:
                self._on_retrieval(ctx.hits)
            except Exception as e:  # noqa: BLE001
                log.warning("on_retrieval 回调异常：%s", e)
        return replace(ctx, messages=list(window))

    def finish_task(self, final_text: str, *, emotion: str = "calm") -> None:
        """非流式路径收尾：与 `reply()` 的收尾段等价（落库/窗口/抽取/情绪/通知）。

        空回复（失败、被取消、预算耗尽、审核拦截）只跳过持久化，不写空 assistant 消息。
        """
        if not (final_text or "").strip():
            return
        self._history.append({"role": "assistant", "content": final_text})
        self._remember("assistant", final_text)
        self._trim_history()
        self._maybe_extract()
        self._persist_emotion(emotion)
        if self._on_reply_complete is not None:
            try:
                self._on_reply_complete(final_text)
            except Exception as e:  # noqa: BLE001 - 钩子异常不影响主流程
                log.warning("on_reply_complete 回调异常：%s", e)

    def reset(self) -> None:
        """开启新会话：清空短期窗口并轮换 session_id，**绝不删库**。"""
        self._history.clear()
        if self._memory is not None:
            try:
                self._memory.new_session()
            except Exception as e:  # noqa: BLE001
                log.warning("轮换会话失败：%s", e)
                self.memory_degraded = True
