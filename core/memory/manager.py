"""记忆中间层：Agent 与存储之间的唯一入口。

分层约定（冻结文档 §3）：

    Agent Core  →  MemoryManager  →  MemoryStore (SQLite)

Agent 永远不直接持有 store。未来的删除 / 合并 / 修改 / 用户查看记忆
全部收敛在本层，避免编排层散落 SQL 与数据形状转换。

Step 2.2 落地「对话记录闭环」：写 conversation、恢复短期上下文、维护会话 id。
Step 2.3 追加「手动记忆 + 人工确认」：记忆的增 / 改 / 删 / 列举，以及
候选队列（propose → 用户确认 → 才进正表）。**本步不调用任何 LLM。**

Step 2.4 落地「记忆读取闭环」：可替换 Embedder（默认 bge-small-zh-v1.5，
纯 stdlib 哈希回退）+ 五通道混合检索（vector/keyword/recency/importance/confidence）
+ MemoryManager.retrieve()/memory_block()/format_block()/reindex()。写入记忆时同步建向量。

Step 2.5 落地「Prompt 记忆注入」：检索命中经 PromptBuilder 定界注入系统提示词
（见 agent.py，编排层只认 MemoryManager.format_block，不直接依赖 retrieval 子层）。

Step 2.6 落地「UI 记忆管理面板」：MemoryPanel 通过本层 list_memories() /
pending_candidates() 取数，confirm_candidate / reject_candidate / forget_id 改数据，
set_active_memories 高亮本轮命中——UI 不碰 store，严守分层。

刻意**不在本步**实现（按冻结文档构建顺序，防止提前复杂化）：
- LLM 自动抽取 process_queue()      → Step 2.7
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Sequence
from uuid import uuid4

from .store import MEMORY_TYPES, MemoryStore
from .embedder import Embedder
from .retrieval import MemoryRetriever, RetrievalHit, format_memory_block

log = logging.getLogger(__name__)

VALID_ROLES = ("user", "assistant")


def new_session_id() -> str:
    """可读且唯一的会话 id：时间戳 + 短随机后缀。"""
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"


def _clamp(value: int, low: int, high: int) -> int:
    """把权重夹进合法区间。

    越界值静默钳制而非抛错：调用方可能是 UI 滑块或未来的 LLM 输出，
    为一个 importance=99 中断整条记忆写入不值得。
    """
    return max(low, min(high, int(value)))


class MemoryManager:
    """对话记录的中间层。

    线程安全由底层 store（RLock）保证；本层自身只持有一个可变字段
    `_session_id`，单进程内由 UI 线程写、worker 线程读，赋值是原子的。
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        session_id: Optional[str] = None,
        embedder: Optional[Embedder] = None,
    ) -> None:
        self._store = store
        self._session_id = session_id or new_session_id()
        # 嵌入层可选：传了就启用向量检索，不传则退化为关键词/权重检索（仍可跑）。
        # 这样「没装 sentence-transformers 的环境」也能完整使用记忆系统。
        self._embedder = embedder
        self._retriever = (
            MemoryRetriever(store, embedder) if embedder is not None else None
        )

    # ----- 会话 -----
    @property
    def session_id(self) -> str:
        return self._session_id

    def new_session(self) -> str:
        """开启新会话（对应 UI 的"新对话"）。

        只轮换 session_id——历史对话仍留在库里，**绝不删库**。
        """
        self._session_id = new_session_id()
        return self._session_id

    # ----- 对话记录 -----
    def add_turn(self, role: str, content: str) -> int:
        """记录一条对话消息，返回 conversation 行 id（跳过时返回 0）。

        - 空白内容不落库（流式失败、用户误触回车都会产生空串）。
        - role 只接受 user / assistant，越界立即抛错而不是静默写脏数据。
        - 用户消息视为「一轮的开始」，同步累加 total_turns 与 last_seen_at。
        """
        if role not in VALID_ROLES:
            raise ValueError(f"未知 role: {role!r}，只接受 {VALID_ROLES}")
        text = (content or "").strip()
        if not text:
            return 0
        msg_id = self._store.add_message(role, text, self._session_id)
        if role == "user":
            self._store.bump_turns(1)
        return msg_id

    def recent_turns(self, limit: int = 40) -> List[Dict[str, str]]:
        """取最近 N 条对话（时间正序），用于冷启动恢复短期上下文。

        跨会话取回：重启后助手仍接得上上次的话题，这是"记得住"的最小体现。
        """
        return self._store.recent_messages(limit=limit)

    def messages_since(self, msg_id: int, limit: int = 200) -> List[Dict[str, object]]:
        """取 id > msg_id 的对话（时间正序），供抽取书签增量处理。

        返回字典含 id / role / content——id 用于推进 last_extract_msg_id，
        避免下一轮重复处理同一批消息。Agent 层只认本方法，不直接碰 store。
        """
        return self._store.messages_since(msg_id, limit=limit)

    # ----- 按日期浏览（时间轴 UI） -----
    def messages_on_day(self, day: str) -> List[Dict[str, object]]:
        """取某天（'YYYY-MM-DD'）全部对话（时间正序），供 UI 按日期回放。"""
        return self._store.messages_on_day(day)

    def days_with_messages(self, limit: int = 92) -> List[str]:
        """有对话的日期列表（新→旧），供日期导航下拉框。"""
        return self._store.days_with_messages(limit=limit)

    def purge_old_conversations(
        self, keep_months: int = 3, *, today: Optional[date] = None
    ) -> int:
        """滚动窗口清理：只保留最近 keep_months 个自然月的原始对话。

        例：keep_months=3、今天是 2026-04-15 → 保留 2/3/4 月，删除 1 月及更早
        （用户规则：到 4 月时把 1 月的数据删掉）。只清 conversation 表，
        长期记忆 / 候选 / 画像不动。返回删除行数。
        today 仅供测试注入，生产走系统日期。
        """
        ref = today or date.today()
        months_back = max(1, keep_months) - 1
        y, m = ref.year, ref.month - months_back
        while m <= 0:
            m += 12
            y -= 1
        window_start = date(y, m, 1).isoformat()
        return self._store.purge_before(window_start)

    # ----- 手动记忆（Step 2.3；无任何 LLM 参与） -----
    def _check_type(self, type: str) -> None:
        if type not in MEMORY_TYPES:
            raise ValueError(f"未知记忆类型: {type!r}，只接受 {MEMORY_TYPES}")

    def remember(
        self,
        type: str,
        content: str,
        *,
        importance: int = 3,
        confidence: int = 3,
        source_msg_id: Optional[int] = None,
    ) -> int:
        """直接写入一条记忆（用户明确要求「记住这个」的路径），返回 memory id。

        返回 0 表示被拒绝：内容为空，或命中黑名单。
        黑名单在**入口**拦截而不是检索时过滤——不该记的东西根本不该落盘。
        """
        self._check_type(type)
        text = (content or "").strip()
        if not text:
            return 0
        if self._store.is_blacklisted(text):
            log.info("记忆被黑名单拦截，未写入: %s", text[:30])
            return 0
        mem_id = self._store.upsert_memory(
            type,
            text,
            confidence=_clamp(confidence, 1, 5),
            importance=_clamp(importance, 1, 10),
            source_msg_id=source_msg_id,
        )
        # 写入记忆时同步建向量：避开「先写后索引」的遗忘窗口——
        # 否则用户刚说「记住这个」，下一轮检索却命中不了。
        self._build_vector(mem_id, text)
        return mem_id

    def list_memories(
        self, types: Sequence[str] = MEMORY_TYPES, limit: int = 20
    ) -> List[Dict[str, object]]:
        """按 importance 降序列举记忆，供用户审阅（Step 2.6 的 UI 数据源）。"""
        return self._store.memories(types=types, limit=limit)

    def edit_memory(
        self,
        mem_id: int,
        *,
        content: Optional[str] = None,
        importance: Optional[int] = None,
        confidence: Optional[int] = None,
        type: Optional[str] = None,
    ) -> bool:
        """修正记错的记忆。类型越界立即抛错，避免写出无法被检索到的孤儿类型。"""
        if type is not None:
            self._check_type(type)
        if content is not None:
            content = content.strip() or None
        return self._store.update_memory(
            mem_id,
            content=content,
            importance=None if importance is None else _clamp(importance, 1, 10),
            confidence=None if confidence is None else _clamp(confidence, 1, 5),
            type=type,
        )

    def confirm_memory(self, mem_id: int) -> bool:
        """用户确认「这条仍然成立」，刷新 last_confirmed_at（为衰减留锚点）。"""
        return self._store.confirm_memory(mem_id)

    def forget_id(self, mem_id: int) -> bool:
        """删除单条记忆，不写黑名单（删这一条 ≠ 永久屏蔽这个话题）。"""
        return self._store.delete_memory(mem_id)

    def forget(self, keyword: str) -> int:
        """按关键词遗忘：删正表 + 入黑名单 + 清候选队列。

        三件事必须一起做。只删正表的话，队列里同主题的待确认项还会再问一次，
        用户会觉得「我明明说了别记」。

        返回**被清除的条目总数**（正表 + 待确认队列）。不只数正表：
        用户眼里候选也是"助手想记的东西"，若只提到某主题、尚未确认就被遗忘，
        回报 0 会让人以为命令没生效。明细走日志。
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return 0
        deleted = self._store.delete_by_keyword(keyword)
        purged = self._store.purge_candidates_by_keyword(keyword)
        log.info("遗忘 %r：删除记忆 %d 条，清理待确认 %d 条", keyword, deleted, purged)
        return deleted + purged

    def blacklist(self) -> List[str]:
        return self._store.blacklist()

    # ----- 检索（Step 2.4） -----
    def _build_vector(self, mem_id: int, content: str) -> None:
        """把一条记忆编码成向量并落库；失败只降级，绝不阻断记忆写入。"""
        if self._embedder is None:
            return
        try:
            vecs = self._embedder.encode([content])
        except Exception as e:  # noqa: BLE001 - 嵌入不可用只降级
            log.warning("向量构建失败 (mem_id=%s)：%s", mem_id, e)
            return
        if vecs:
            self._store.save_vector(mem_id, self._embedder.name, vecs[0])

    def retrieve(self, query: str, *, top_k: int = 5) -> List[RetrievalHit]:
        """按混合分检索最相关记忆；未启用嵌入层时返回空列表。

        任何检索异常都被吞掉降级为空结果——"想起往事"失败时，
        宁可助手这一轮不引用记忆，也不能让整段对话崩掉。
        """
        if self._retriever is None:
            return []
        try:
            return self._retriever.retrieve(query, top_k=top_k)
        except Exception as e:  # noqa: BLE001
            log.warning("记忆检索失败：%s", e)
            return []

    def memory_block(self, query: str, *, top_k: int = 5) -> str:
        """检索并把命中渲染成可注入提示词的段落（已剔除分数）。"""
        return format_memory_block(self.retrieve(query, top_k=top_k))

    def format_block(self, hits: List[RetrievalHit]) -> str:
        """把已算好的命中渲染成可注入提示词的段落（已剔除分数）。

        存在意义是分层：Agent 编排层**不该**知道 retrieval.format_memory_block
        这个子层函数——它只认 MemoryManager。通过本方法，agent.py 不再
        直接 import core.memory.retrieval（见 Step 2.6 清债 #1）。
        """
        return format_memory_block(hits)

    def reindex(self, limit: int = 200) -> int:
        """为缺失 / 失效向量补算。换嵌入模型、或存量记忆未建向量时调用。

        返回实际建好的向量条数，便于日志与 Inspector 展示进度。
        """
        if self._embedder is None:
            return 0
        rows = self._store.memories_missing_vector(self._embedder.name, limit=limit)
        done = 0
        for r in rows:
            try:
                vecs = self._embedder.encode([r["content"]])
            except Exception as e:  # noqa: BLE001
                log.warning("reindex 向量失败 (mem_id=%s)：%s", r["id"], e)
                continue
            if vecs:
                self._store.save_vector(r["id"], self._embedder.name, vecs[0])
                done += 1
        return done

    # ----- 候选队列（人工确认闸门） -----
    def propose(
        self,
        type: str,
        content: str,
        *,
        importance: int = 3,
        confidence: int = 3,
        reason: Optional[str] = None,
        source_msg_id: Optional[int] = None,
    ) -> int:
        """提交待确认候选，返回候选 id；被拒绝（空 / 黑名单 / 已决策）时返回 0。

        Step 2.7 的 LLM 抽取也只能走这条路——自动抽取的记忆一律先经人工确认，
        避免「用户说今天天气不错 → AI 记住用户喜欢晴天」这类污染。
        """
        self._check_type(type)
        text = (content or "").strip()
        if not text:
            return 0
        if self._store.is_blacklisted(text):
            log.info("候选被黑名单拦截: %s", text[:30])
            return 0
        return self._store.add_candidate(
            type,
            text,
            confidence=_clamp(confidence, 1, 5),
            importance=_clamp(importance, 1, 10),
            reason=reason,
            source_msg_id=source_msg_id,
        )

    def pending_candidates(self, limit: int = 20) -> List[Dict[str, object]]:
        return self._store.pending_candidates(limit=limit)

    def confirm_candidate(self, cand_id: int) -> int:
        """用户点「记住」：候选转正，返回 memory id（无效或已决策返回 0）。

        转正后立刻补向量：候选转正走的是 store 直写、不经过 remember()，
        若不在这里补，这批记忆会一直缺向量，直到某次 reindex() 才被唤醒。
        """
        mem_id = self._store.confirm_candidate(cand_id)
        if mem_id:
            row = self._store.memory(mem_id)
            if row:
                self._build_vector(mem_id, row["content"])
        return mem_id

    def reject_candidate(self, cand_id: int) -> bool:
        """用户点「不用记」：标记 rejected，此后同内容不再打扰。"""
        return self._store.reject_candidate(cand_id)

    def update_candidate(
        self,
        cand_id: int,
        *,
        type: Optional[str] = None,
        content: Optional[str] = None,
        importance: Optional[int] = None,
        confidence: Optional[int] = None,
    ) -> bool:
        """人修改一条待确认候选（M3.2「修改」）。

        AI 的草稿由人定稿：类型越界立即抛错，权重钳进合法区间，
        空白/黑名单内容拦截在入口——不该记的东西连候选草稿都不该留。
        改完仍是 pending，由人决定下一步确认或拒绝。
        """
        if type is not None:
            self._check_type(type)
        if content is not None:
            content = content.strip() or None
            if not content:
                return False
            if self._store.is_blacklisted(content):
                return False
        return self._store.update_candidate(
            cand_id,
            type=type,
            content=content,
            importance=None if importance is None else _clamp(importance, 1, 10),
            confidence=None if confidence is None else _clamp(confidence, 1, 5),
        )

    # ----- Agent 状态（persona_state，与用户记忆分表） -----
    def state(self) -> Dict[str, object]:
        return self._store.load_state()

    def bump_companionship(self, seconds: int) -> int:
        """累加今日陪伴秒数（跨天由 store 归零），返回累加后的值。"""
        return self._store.bump_companionship(seconds)

    def save_state(self, **fields) -> None:
        """写回 persona_state（trust/stage 等系统状态）。由 RelationshipManager 调用。

        仅透传 store.save_state，字段受 _STATE_FIELDS 白名单约束——Agent 层不直接碰 store。
        """
        self._store.save_state(**fields)

    # ----- 生命周期 -----
    def close(self) -> None:
        self._store.close()
