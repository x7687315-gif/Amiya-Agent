"""记忆存储层：MemoryStore 协议 + SQLiteMemoryStore 实现。

Phase 2-A Step 2.1（架构已冻结，仅做存储层）：
- 统一 `memory` 表（type 维度：fact/preference/event/goal/relationship）
- 另含 `conversation`（原始对话）、`user_profile`（用户画像）、
  `persona_state`（Agent 自身状态，与用户记忆严格分表）、`memory_blacklist`（memory_control）
- 线程安全：单连接 + RLock + WAL；支持 ":memory:" 供测试
- 零外部依赖（sqlite3 为 stdlib）

Step 2.3 追加（schema v2）：
- `memory_candidate` 人工确认队列 + 记忆按 id 的改 / 删 / 确认
- 迁移改为版本链，已装机的 v1 库可平滑升级

V1 预留但暂不参与逻辑的字段：
- `memory.decay_rate` / `last_confirmed_at`：记忆衰减（Phase 2-C 后实现）
- `save_vector` 的 `model` 参数：V1 单模型直写 BLOB；后续可迁到独立 vectors 表以支持多模型重嵌
"""
from __future__ import annotations

import array
import os
import sqlite3
import threading
from datetime import date, datetime
from typing import Dict, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

MEMORY_TYPES = ("fact", "preference", "event", "goal", "relationship")
CANDIDATE_STATUSES = ("pending", "confirmed", "rejected")
SCHEMA_VERSION = 2

# persona_state 允许被 save_state 改写的字段（防止任意键写入）
_STATE_FIELDS = {
    "trust",
    "companionship_seconds",
    "companionship_day",
    "emotion",
    "total_turns",
    "last_extract_msg_id",
    "last_seen_at",
}

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS conversation(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_ts ON conversation(ts);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversation(session_id, id);

CREATE TABLE IF NOT EXISTS memory(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  content TEXT NOT NULL,
  confidence INTEGER DEFAULT 3,
  importance INTEGER DEFAULT 3,
  hits INTEGER DEFAULT 0,
  source_msg_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  embedding BLOB,
  decay_rate REAL DEFAULT 0.0,
  last_confirmed_at TEXT,
  UNIQUE(type, content)
);
CREATE INDEX IF NOT EXISTS idx_mem_type ON memory(type, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_mem_imp ON memory(importance DESC, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_mem_conf ON memory(confidence DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS user_profile(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persona_state(
  id INTEGER PRIMARY KEY CHECK(id=1),
  trust INTEGER DEFAULT 70,
  companionship_seconds INTEGER DEFAULT 0,
  companionship_day TEXT DEFAULT '',
  emotion TEXT DEFAULT 'calm',
  total_turns INTEGER DEFAULT 0,
  last_extract_msg_id INTEGER DEFAULT 0,
  last_seen_at TEXT DEFAULT ''
);
INSERT OR IGNORE INTO persona_state(id) VALUES(1);

CREATE TABLE IF NOT EXISTS memory_blacklist(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keyword TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
"""

# v2（Step 2.3）：人工确认队列。
# 设计意图——长期陪伴 Agent 最大的风险不是"忘记"，而是"记错"。
# 任何记忆（含未来 LLM 抽取的）都先落候选，用户确认后才进 memory 正表。
_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS memory_candidate(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  content TEXT NOT NULL,
  confidence INTEGER DEFAULT 3,
  importance INTEGER DEFAULT 3,
  reason TEXT,
  source_msg_id INTEGER,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  decided_at TEXT,
  memory_id INTEGER,
  UNIQUE(type, content)
);
CREATE INDEX IF NOT EXISTS idx_cand_status ON memory_candidate(status, id DESC);
"""

# 版本链：按序执行所有「库版本 < 目标版本」的脚本。
# 已装机的 v1 库再次打开时只会跑 v2，不会重放 v1。
_MIGRATIONS: Tuple[Tuple[int, str], ...] = (
    (1, _SCHEMA_V1),
    (2, _SCHEMA_V2),
)


@runtime_checkable
class MemoryStore(Protocol):
    """上层（MemoryManager / Agent）只依赖这个协议。"""

    def add_message(self, role: str, content: str, session_id: str) -> int: ...
    def recent_messages(self, limit: int = 40) -> List[Dict[str, str]]: ...
    def messages_since(self, msg_id: int, limit: int = 200) -> List[Dict[str, str]]: ...
    def upsert_memory(
        self,
        type: str,
        content: str,
        *,
        confidence: int = 3,
        importance: int = 3,
        source_msg_id: Optional[int] = None,
    ) -> int: ...
    def set_profile(self, key: str, value: str) -> None: ...
    def get_profile(self, key: str) -> Optional[str]: ...
    def memories(
        self, types: Sequence[str] = MEMORY_TYPES, limit: int = 8
    ) -> List[Dict[str, object]]: ...
    def memory(self, mem_id: int) -> Optional[Dict[str, object]]: ...
    def update_memory(
        self,
        mem_id: int,
        *,
        content: Optional[str] = None,
        importance: Optional[int] = None,
        confidence: Optional[int] = None,
        type: Optional[str] = None,
    ) -> bool: ...
    def delete_memory(self, mem_id: int) -> bool: ...
    def confirm_memory(self, mem_id: int) -> bool: ...
    def events(self, limit: int = 8) -> List[Tuple[str, str]]: ...
    def goals(self, limit: int = 8) -> List[str]: ...
    def add_candidate(
        self,
        type: str,
        content: str,
        *,
        confidence: int = 3,
        importance: int = 3,
        reason: Optional[str] = None,
        source_msg_id: Optional[int] = None,
    ) -> int: ...
    def pending_candidates(self, limit: int = 20) -> List[Dict[str, object]]: ...
    def candidate(self, cand_id: int) -> Optional[Dict[str, object]]: ...
    def confirm_candidate(self, cand_id: int) -> int: ...
    def reject_candidate(self, cand_id: int) -> bool: ...
    def purge_candidates_by_keyword(self, keyword: str) -> int: ...
    def load_state(self) -> Dict[str, object]: ...
    def save_state(self, **fields) -> None: ...
    def bump_turns(self, n: int = 1) -> int: ...
    def bump_companionship(self, seconds: int) -> int: ...
    def save_vector(self, mem_id: int, model: str, vec: Sequence[float]) -> None: ...
    def vector_of(self, mem_id: int) -> Optional[List[float]]: ...
    def add_blacklist(self, keyword: str) -> None: ...
    def is_blacklisted(self, text: str) -> bool: ...
    def delete_by_keyword(self, keyword: str) -> int: ...
    def blacklist(self) -> List[str]: ...
    def close(self) -> None: ...


class SQLiteMemoryStore:
    """基于 stdlib sqlite3 的 MemoryStore 实现。

    线程安全：所有读写都在 RLock 内；单连接 `check_same_thread=False`，
    配合 WAL 以支撑后台抽取线程与 UI 线程并发。
    """

    def __init__(self, db_path: str = "data/assistant.db") -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            parent = os.path.dirname(db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if db_path != ":memory:":
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:  # pragma: no cover - 极少数平台不支持
                pass
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._migrate()

    # ----- 迁移 -----
    def _migrate(self) -> None:
        """按版本链增量升级，每步单独提交并推进 user_version。

        逐版本推进（而非一次性跳到最新）保证中途失败时库不会停在
        「表建了一半但版本号已是最新」的不可修复状态。
        """
        with self._lock:
            cur = self._conn.execute("PRAGMA user_version").fetchone()
            version = cur[0] if cur else 0
            for target, script in _MIGRATIONS:
                if version < target:
                    self._conn.executescript(script)
                    self._conn.execute(f"PRAGMA user_version={target}")
                    self._conn.commit()
                    version = target

    # ----- 对话 -----
    def add_message(self, role: str, content: str, session_id: str) -> int:
        now = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO conversation(ts, session_id, role, content) VALUES(?,?,?,?)",
                (now, session_id, role, content),
            )
            self._conn.commit()
            return cur.lastrowid

    def recent_messages(self, limit: int = 40) -> List[Dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM conversation ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"role": r["role"], "content": r["content"]} for r in reversed(rows)
        ]

    def messages_since(self, msg_id: int, limit: int = 200) -> List[Dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM conversation WHERE id > ? "
                "ORDER BY id ASC LIMIT ?",
                (msg_id, limit),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    # ----- 记忆 -----
    def upsert_memory(
        self,
        type: str,
        content: str,
        *,
        confidence: int = 3,
        importance: int = 3,
        source_msg_id: Optional[int] = None,
    ) -> int:
        now = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memory(type, content, confidence, importance, "
                "source_msg_id, created_at, updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(type, content) DO UPDATE SET "
                "confidence=excluded.confidence, "
                "importance=excluded.importance, "
                "updated_at=excluded.updated_at",
                (type, content, confidence, importance, source_msg_id, now, now),
            )
            if cur.lastrowid:
                mem_id = cur.lastrowid
            else:
                mem_id = self._conn.execute(
                    "SELECT id FROM memory WHERE type=? AND content=?",
                    (type, content),
                ).fetchone()[0]
            self._conn.commit()
            return mem_id

    def memories(
        self, types: Sequence[str] = MEMORY_TYPES, limit: int = 8
    ) -> List[Dict[str, object]]:
        placeholders = ",".join("?" * len(types))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, type, content, confidence, importance FROM memory "
                f"WHERE type IN ({placeholders}) "
                f"ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (*types, limit),
            ).fetchall()
        return [
            {
                "id": r["id"],
                "type": r["type"],
                "content": r["content"],
                "confidence": r["confidence"],
                "importance": r["importance"],
            }
            for r in rows
        ]

    def memory(self, mem_id: int) -> Optional[Dict[str, object]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, type, content, confidence, importance, hits, "
                "created_at, updated_at, last_confirmed_at FROM memory WHERE id=?",
                (mem_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_memory(
        self,
        mem_id: int,
        *,
        content: Optional[str] = None,
        importance: Optional[int] = None,
        confidence: Optional[int] = None,
        type: Optional[str] = None,
    ) -> bool:
        """按 id 修改记忆，返回是否命中。None 的字段保持原值。

        用户能改错记忆，是长期陪伴 Agent 的必要能力——"记错"比"忘记"更伤。
        """
        fields = {
            "type": type,
            "content": content,
            "importance": importance,
            "confidence": confidence,
        }
        pairs = [(k, v) for k, v in fields.items() if v is not None]
        if not pairs:
            return False
        sets = ", ".join(f"{k}=?" for k, _ in pairs)
        now = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE memory SET {sets}, updated_at=? WHERE id=?",
                [v for _, v in pairs] + [now, mem_id],
            )
            self._conn.commit()
            return cur.rowcount > 0

    def delete_memory(self, mem_id: int) -> bool:
        """按 id 精确删除。与 delete_by_keyword 不同，不写黑名单——
        用户删单条记忆未必想永久屏蔽该主题。"""
        with self._lock:
            cur = self._conn.execute("DELETE FROM memory WHERE id=?", (mem_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def confirm_memory(self, mem_id: int) -> bool:
        """用户确认「这条仍然成立」，刷新 last_confirmed_at。

        为 Phase 2-C 的记忆衰减留下真实时间锚点：越久没被确认的记忆，
        有效重要度越低。本步只记录，不参与排序。
        """
        now = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memory SET last_confirmed_at=?, updated_at=? WHERE id=?",
                (now, now, mem_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def events(self, limit: int = 8) -> List[Tuple[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT created_at, content FROM memory WHERE type='event' "
                "ORDER BY created_at DESC, updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [(r["created_at"], r["content"]) for r in rows]

    def goals(self, limit: int = 8) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT content FROM memory WHERE type='goal' "
                "ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [r["content"] for r in rows]

    # ----- 候选队列（人工确认闸门） -----
    def add_candidate(
        self,
        type: str,
        content: str,
        *,
        confidence: int = 3,
        importance: int = 3,
        reason: Optional[str] = None,
        source_msg_id: Optional[int] = None,
    ) -> int:
        """提交一条待确认候选，返回候选 id；被用户否决过的内容返回 0。

        `status='rejected'` 的记录**不会**被重新唤醒为 pending——
        否则用户每拒绝一次，助手下轮又问一遍，等于骚扰。
        pending 状态下重复提交则更新权重（同一件事反复提到，说明更重要）。
        """
        now = datetime.now().isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT id, status FROM memory_candidate WHERE type=? AND content=?",
                (type, content),
            ).fetchone()
            if row is not None:
                if row["status"] != "pending":
                    return 0  # 已确认或已否决，不重复打扰
                self._conn.execute(
                    "UPDATE memory_candidate SET importance=?, confidence=?, "
                    "reason=COALESCE(?, reason) WHERE id=?",
                    (importance, confidence, reason, row["id"]),
                )
                self._conn.commit()
                return row["id"]
            cur = self._conn.execute(
                "INSERT INTO memory_candidate(type, content, confidence, importance, "
                "reason, source_msg_id, status, created_at) VALUES(?,?,?,?,?,?, 'pending', ?)",
                (type, content, confidence, importance, reason, source_msg_id, now),
            )
            self._conn.commit()
            return cur.lastrowid

    def pending_candidates(self, limit: int = 20) -> List[Dict[str, object]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, type, content, confidence, importance, reason, "
                "source_msg_id, created_at FROM memory_candidate "
                "WHERE status='pending' ORDER BY importance DESC, id ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def candidate(self, cand_id: int) -> Optional[Dict[str, object]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, type, content, confidence, importance, reason, "
                "source_msg_id, status, created_at, decided_at, memory_id "
                "FROM memory_candidate WHERE id=?",
                (cand_id,),
            ).fetchone()
        return dict(row) if row else None

    def confirm_candidate(self, cand_id: int) -> int:
        """确认候选 → 写入 memory 正表，返回 memory id（无效/已决策返回 0）。

        写正表与改候选状态必须在同一把锁内完成，否则并发确认会写出两条记忆。
        """
        now = datetime.now().isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT type, content, confidence, importance, source_msg_id, status "
                "FROM memory_candidate WHERE id=?",
                (cand_id,),
            ).fetchone()
            if row is None or row["status"] != "pending":
                return 0
            cur = self._conn.execute(
                "INSERT INTO memory(type, content, confidence, importance, "
                "source_msg_id, created_at, updated_at, last_confirmed_at) "
                "VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(type, content) DO UPDATE SET "
                "confidence=excluded.confidence, importance=excluded.importance, "
                "updated_at=excluded.updated_at, "
                "last_confirmed_at=excluded.last_confirmed_at",
                (
                    row["type"],
                    row["content"],
                    row["confidence"],
                    row["importance"],
                    row["source_msg_id"],
                    now,
                    now,
                    now,
                ),
            )
            mem_id = cur.lastrowid or self._conn.execute(
                "SELECT id FROM memory WHERE type=? AND content=?",
                (row["type"], row["content"]),
            ).fetchone()[0]
            self._conn.execute(
                "UPDATE memory_candidate SET status='confirmed', decided_at=?, "
                "memory_id=? WHERE id=?",
                (now, mem_id, cand_id),
            )
            self._conn.commit()
            return mem_id

    def reject_candidate(self, cand_id: int) -> bool:
        now = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memory_candidate SET status='rejected', decided_at=? "
                "WHERE id=? AND status='pending'",
                (now, cand_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def purge_candidates_by_keyword(self, keyword: str) -> int:
        """清掉候选队列里匹配关键词的待确认项，返回清理条数。

        与 delete_by_keyword 配套：用户说「别记这个」时，若只删正表不清队列，
        同一条内容会从候选里再冒出来问一次。
        """
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memory_candidate WHERE status='pending' AND content LIKE ?",
                (f"%{keyword}%",),
            )
            self._conn.commit()
            return cur.rowcount

    # ----- 用户画像 -----
    def set_profile(self, key: str, value: str) -> None:
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO user_profile(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (key, value, now),
            )
            self._conn.commit()

    def get_profile(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM user_profile WHERE key=?", (key,)
            ).fetchone()
        return row["value"] if row else None

    # ----- Agent 状态 -----
    def load_state(self) -> Dict[str, object]:
        with self._lock:
            row = self._conn.execute(
                "SELECT trust, companionship_seconds, companionship_day, emotion, "
                "total_turns, last_extract_msg_id, last_seen_at FROM persona_state "
                "WHERE id=1"
            ).fetchone()
        return {
            "trust": row["trust"],
            "companionship_seconds": row["companionship_seconds"],
            "companionship_day": row["companionship_day"],
            "emotion": row["emotion"],
            "total_turns": row["total_turns"],
            "last_extract_msg_id": row["last_extract_msg_id"],
            "last_seen_at": row["last_seen_at"],
        }

    def save_state(self, **fields) -> None:
        pairs = [(k, fields[k]) for k in fields if k in _STATE_FIELDS]
        if not pairs:
            return
        sets = ", ".join(f"{k}=?" for k, _ in pairs)
        with self._lock:
            self._conn.execute(
                f"UPDATE persona_state SET {sets} WHERE id=1",
                [v for _, v in pairs],
            )
            self._conn.commit()

    def bump_turns(self, n: int = 1) -> int:
        """原子累加对话轮次并刷新 last_seen_at，返回累加后的总轮次。

        必须在锁内一次完成读改写：MemoryManager 若用 load_state + save_state
        两步实现，后台抽取线程与 UI 线程并发时会丢计数。
        """
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE persona_state SET total_turns=total_turns+?, last_seen_at=? "
                "WHERE id=1",
                (n, now),
            )
            row = self._conn.execute(
                "SELECT total_turns FROM persona_state WHERE id=1"
            ).fetchone()
            self._conn.commit()
            return row["total_turns"]

    def bump_companionship(self, seconds: int) -> int:
        today = date.today().isoformat()
        with self._lock:
            row = self._conn.execute(
                "SELECT companionship_seconds, companionship_day FROM persona_state "
                "WHERE id=1"
            ).fetchone()
            current, day = row["companionship_seconds"], row["companionship_day"]
            if day != today:  # 跨天：陪伴时长归零后续算
                current = 0
            current += seconds
            self._conn.execute(
                "UPDATE persona_state SET companionship_seconds=?, companionship_day=? "
                "WHERE id=1",
                (current, today),
            )
            self._conn.commit()
            return current

    # ----- 向量（V1 直写 BLOB；model 参数预留多模型重嵌） -----
    def save_vector(self, mem_id: int, model: str, vec: Sequence[float]) -> None:
        blob = array.array("f", vec).tobytes()
        with self._lock:
            self._conn.execute(
                "UPDATE memory SET embedding=? WHERE id=?", (blob, mem_id)
            )
            self._conn.commit()

    def vector_of(self, mem_id: int) -> Optional[List[float]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT embedding FROM memory WHERE id=?", (mem_id,)
            ).fetchone()
        if not row or row["embedding"] is None:
            return None
        return list(array.array("f", row["embedding"]))

    # ----- memory_control -----
    def add_blacklist(self, keyword: str) -> None:
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_blacklist(keyword, created_at) VALUES(?,?)",
                (keyword, now),
            )
            self._conn.commit()

    def is_blacklisted(self, text: str) -> bool:
        with self._lock:
            rows = self._conn.execute(
                "SELECT keyword FROM memory_blacklist"
            ).fetchall()
        return any(kw in text for (kw,) in rows)

    def delete_by_keyword(self, keyword: str) -> int:
        now = datetime.now().isoformat()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memory WHERE content LIKE ?", (f"%{keyword}%",)
            )
            deleted = cur.rowcount
            self._conn.execute(
                "INSERT OR IGNORE INTO memory_blacklist(keyword, created_at) VALUES(?,?)",
                (keyword, now),
            )
            self._conn.commit()
            return deleted

    def blacklist(self) -> List[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT keyword FROM memory_blacklist ORDER BY id"
            ).fetchall()
        return [r["keyword"] for r in rows]

    # ----- 生命周期 -----
    def close(self) -> None:
        with self._lock:
            self._conn.close()
