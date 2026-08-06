"""记忆中间层：Agent 与存储之间的唯一入口。

分层约定（冻结文档 §3）：

    Agent Core  →  MemoryManager  →  MemoryStore (SQLite)

Agent 永远不直接持有 store。未来的删除 / 合并 / 修改 / 用户查看记忆
全部收敛在本层，避免编排层散落 SQL 与数据形状转换。

Phase 2-A Step 2.2 严格边界——本步只做「对话记录闭环」：
写 conversation、恢复短期上下文、维护会话 id 与 Agent 状态计数。

刻意**不在本步**实现（按冻结文档构建顺序，防止提前复杂化）：
- 手动记忆写入 / 删除 / 列举       → Step 2.3
- 向量与 retrieve() 检索            → Step 2.4
- Prompt 记忆注入                   → Step 2.5
- LLM 自动抽取 process_queue()      → Step 2.7
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

from .store import MemoryStore

log = logging.getLogger(__name__)

VALID_ROLES = ("user", "assistant")


def new_session_id() -> str:
    """可读且唯一的会话 id：时间戳 + 短随机后缀。"""
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"


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
    ) -> None:
        self._store = store
        self._session_id = session_id or new_session_id()

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

    # ----- Agent 状态（persona_state，与用户记忆分表） -----
    def state(self) -> Dict[str, object]:
        return self._store.load_state()

    def bump_companionship(self, seconds: int) -> int:
        """累加今日陪伴秒数（跨天由 store 归零），返回累加后的值。"""
        return self._store.bump_companionship(seconds)

    # ----- 生命周期 -----
    def close(self) -> None:
        self._store.close()
