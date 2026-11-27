"""研究会话：把服务端工具的协议状态存下来，供后续追问原样回传。

为什么必须存（已实测，见 `docs/2x/RESEARCH_SESSION_EVIDENCE.md`）：
`deepseek_read_url` 的页面正文加密在 `signature` 里，`data` 恒为空串。
不回传整条 assistant 消息时：① 工具关闭 → 页面彻底丢失；② 工具开启 →
模型**静默重搜**（答案看起来正常，但成本翻倍、来源可能漂移）。

设计取舍：
- `protocol_json` **原样存**服务端返回的 assistant 消息，不解析成业务字段——
  协议可能变，解析后再存会随版本漂移。
- **不存网页正文**：正文本就加密在 signature 里，我们也解不开；存了只会制造
  "我们拥有正文"的错觉。
- 单表 + JSON 列，够用即可；到期清理靠 `expires_at`。
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .memory.store import SQLiteMemoryStore

log = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"
STATUS_EXPIRED = "expired"

# 单会话状态（对 UI 暴露的三个值；blocked 只在显式关闭自动重启时出现）
STATE_CONTINUED = "continued"
STATE_RESTARTED = "restarted"
STATE_BLOCKED = "blocked"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@dataclass(frozen=True)
class ResearchSession:
    session_id: str
    status: str
    created_at: str
    updated_at: str
    expires_at: Optional[str]
    turn_count: int = 0
    total_chars: int = 0
    protocol: Optional[dict] = None     # {"assistant": {...}, "user": "..."}
    sources: List[dict] = None          # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sources is None:
            object.__setattr__(self, "sources", [])

    def protocol_message(self) -> Optional[dict]:
        """取回可直接放进 messages 的 assistant 消息。"""
        if not isinstance(self.protocol, dict):
            return None
        msg = self.protocol.get("assistant")
        return msg if isinstance(msg, dict) else None

    def last_user_text(self) -> str:
        if isinstance(self.protocol, dict):
            return str(self.protocol.get("user") or "")
        return ""


class ResearchSessionRepository:
    """复用 MemoryStore 的单连接与 RLock（同一 db 文件，不另开连接）。"""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        ttl_hours: float = 24.0,
        max_chars: int = 60_000,
    ) -> None:
        self._store = store
        self._ttl = timedelta(hours=ttl_hours)
        self._max_chars = max_chars

    # ----- 基础 CRUD -----
    def create(self, conversation_id: Optional[str] = None) -> ResearchSession:
        now = _now()
        sid = f"rs_{uuid.uuid4().hex[:12]}"
        with self._store._lock:  # noqa: SLF001 - 同库单连接，与既有仓储一致
            self._store._conn.execute(
                "INSERT INTO research_session(session_id, conversation_id, status,"
                " created_at, updated_at, turn_count, total_chars, expires_at)"
                " VALUES(?,?,?,?,?,0,0,?)",
                (sid, conversation_id, STATUS_ACTIVE, _iso(now), _iso(now),
                 _iso(now + self._ttl)),
            )
            self._store._conn.commit()
        return ResearchSession(session_id=sid, status=STATUS_ACTIVE,
                               created_at=_iso(now), updated_at=_iso(now),
                               expires_at=_iso(now + self._ttl))

    def get(self, session_id: str) -> Optional[ResearchSession]:
        with self._store._lock:  # noqa: SLF001
            row = self._store._conn.execute(
                "SELECT * FROM research_session WHERE session_id=?", (session_id,)
            ).fetchone()
        if row is None:
            return None
        sess = self._row_to_session(row)
        # 惰性过期：读到过期会话就标记（不改行为，只让状态可见）
        if sess.status == STATUS_ACTIVE and _is_past(sess.expires_at):
            self.close(sess.session_id, status=STATUS_EXPIRED)
            sess = ResearchSession(**{**sess.__dict__, "status": STATUS_EXPIRED})
        return sess

    def record_turn(
        self,
        session_id: str,
        *,
        user_text: str,
        assistant_message: dict,
        sources: Optional[List[dict]] = None,
    ) -> None:
        """保存本轮协议状态（assistant 消息原样 + 本轮用户提问）。"""
        payload = json.dumps(
            {"assistant": assistant_message, "user": user_text},
            ensure_ascii=False,
        )
        now = _now()
        srcs = json.dumps(sources or [], ensure_ascii=False)
        with self._store._lock:  # noqa: SLF001
            self._store._conn.execute(
                "UPDATE research_session SET protocol_json=?, sources_json=?,"
                " updated_at=?, turn_count=turn_count+1,"
                " total_chars=total_chars+? WHERE session_id=?",
                (payload, srcs, _iso(now), len(user_text), session_id),
            )
            self._store._conn.commit()

    def close(self, session_id: str, *, status: str = STATUS_CLOSED) -> None:
        with self._store._lock:  # noqa: SLF001
            self._store._conn.execute(
                "UPDATE research_session SET status=?, updated_at=? WHERE session_id=?",
                (status, _iso(_now()), session_id),
            )
            self._store._conn.commit()

    def cleanup(self) -> int:
        """清理过期 / 已关闭超过 7 天的会话，返回删除行数。"""
        cutoff = _iso(_now() - timedelta(days=7))
        with self._store._lock:  # noqa: SLF001
            cur = self._store._conn.execute(
                "DELETE FROM research_session WHERE expires_at < ?"
                " OR (status IN (?,?) AND updated_at < ?)",
                (_iso(_now()), STATUS_CLOSED, STATUS_EXPIRED, cutoff),
            )
            self._store._conn.commit()
            return cur.rowcount

    # ----- 完整性检查 -----
    def check(self, session: Optional[ResearchSession]) -> Tuple[bool, str]:
        """能否安全延续。返回 (ok, reason)。

        规则只覆盖**已实测**的约束：会话有效、协议存在、assistant 带
        server_tool_calls 且有 id、体积未超限。其余（字段子集、顺序）未验证，
        因此采取"原样回传"这个最保守做法，而不是逐项校验后重排。
        """
        if session is None:
            return False, "no_session"
        if session.status != STATUS_ACTIVE:
            return False, f"status_{session.status}"
        if _is_past(session.expires_at):
            return False, "expired"
        msg = session.protocol_message()
        if not msg:
            return False, "no_protocol"
        blocks = msg.get("server_tool_calls")
        if not isinstance(blocks, list) or not blocks:
            return False, "no_tool_calls"
        # 结构是 [{index, calls:[{id,...}]}]，id 在**内层 call** 上，不在 block 上
        calls = [c for b in blocks if isinstance(b, dict)
                 for c in (b.get("calls") or []) if isinstance(c, dict)]
        if not calls:
            return False, "no_tool_calls"
        if not any(c.get("id") for c in calls):
            return False, "missing_call_id"
        if session.total_chars > self._max_chars:
            return False, "too_large"
        return True, ""

    # ----- 内部 -----
    def _row_to_session(self, row) -> ResearchSession:
        try:
            protocol = json.loads(row["protocol_json"]) if row["protocol_json"] else None
        except (TypeError, ValueError):
            protocol = None
        try:
            sources = json.loads(row["sources_json"]) if row["sources_json"] else []
        except (TypeError, ValueError):
            sources = []
        return ResearchSession(
            session_id=row["session_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
            turn_count=int(row["turn_count"] or 0),
            total_chars=int(row["total_chars"] or 0),
            protocol=protocol,
            sources=sources,
        )


def _is_past(ts: Optional[str]) -> bool:
    if not ts:
        return True
    try:
        return datetime.fromisoformat(ts) <= _now()
    except ValueError:
        return True
