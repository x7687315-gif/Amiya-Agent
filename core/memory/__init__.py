"""记忆包（Phase 2-A）。

暴露存储协议、SQLite 实现与中间层 MemoryManager，不依赖任何外部库
（sqlite3 为 stdlib）。已落地 Step 2.1 存储层、2.2 对话闭环、2.3 手动记忆
与人工确认队列；Embedder / Retriever / Extractor 在后续 Step 实现：
- Step 2.4 检索、2.5 Prompt 增强、2.6 UI 记忆面板、2.7 自动抽取。
"""
from __future__ import annotations

from .manager import MemoryManager, new_session_id
from .store import (
    CANDIDATE_STATUSES,
    MEMORY_TYPES,
    MemoryStore,
    SQLiteMemoryStore,
)

__all__ = [
    "MemoryStore",
    "SQLiteMemoryStore",
    "MemoryManager",
    "new_session_id",
    "MEMORY_TYPES",
    "CANDIDATE_STATUSES",
]
