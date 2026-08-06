"""记忆包（Phase 2-A）。

暴露存储协议、SQLite 实现与中间层 MemoryManager，不依赖任何外部库
（sqlite3 为 stdlib）。Embedder / Retriever / Extractor 在后续 Step 实现：
- Step 2.3 手动记忆写入、2.4 检索、2.5 Prompt 增强、2.7 自动抽取。
"""
from __future__ import annotations

from .manager import MemoryManager, new_session_id
from .store import MemoryStore, SQLiteMemoryStore

__all__ = [
    "MemoryStore",
    "SQLiteMemoryStore",
    "MemoryManager",
    "new_session_id",
]
