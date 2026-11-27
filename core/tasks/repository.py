"""TaskRepository：assistant_task 表的 CRUD 门面。

设计纪律（docs/2x/DATA_MODEL_2.0.md §3.3）：
- 删除 = 状态变更（status='cancelled'），不物理删数据（L2 审批由 Tool 层处理）。
- 任务数据独立：不写 memory、不写 conversation、无跨表触发器。
- 复用 MemoryStore 的 SQLite 连接与 RLock（同一 db 文件），不另开连接。
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from core.memory.store import SQLiteMemoryStore


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    description: str = ""
    due_at: Optional[str] = None      # ISO 8601 或 None
    priority: int = 5
    status: str = "open"              # open | done | cancelled
    notified_at: Optional[str] = None  # 到期提醒已触发的时间（v5；null=未提醒）

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id, "title": self.title,
            "description": self.description, "due_at": self.due_at,
            "priority": self.priority, "status": self.status,
            "notified_at": self.notified_at,
        }


class TaskRepository:
    """任务仓储。共享给定 store 的连接与锁，保持单连接 WAL 语义。"""

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._store = store
        self._conn = store._conn          # 有意复用：同一 db 单连接，避免锁梯度复杂
        self._lock = store._lock

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def create(
        self,
        title: str,
        *,
        description: str = "",
        due_at: Optional[str] = None,
        priority: int = 5,
    ) -> str:
        """新建任务，返回 task_id。title 为空拒绝。"""
        title = (title or "").strip()
        if not title:
            raise ValueError("任务标题不得为空")
        if not 1 <= priority <= 10:
            raise ValueError("priority 须在 1-10")
        task_id = uuid.uuid4().hex
        now = self._now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO assistant_task "
                "(task_id,title,description,due_at,priority,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,'open',?,?)",
                (task_id, title, description, due_at or None, priority, now, now),
            )
            self._conn.commit()
        return task_id

    def list(self, *, status: Optional[str] = None, limit: int = 20) -> List[Task]:
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM assistant_task WHERE status=? "
                    "ORDER BY priority, created_at LIMIT ?", (status, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM assistant_task ORDER BY priority, created_at LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM assistant_task WHERE task_id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def update(self, task_id: str, **fields) -> bool:
        """更新白名单字段；无变化或不存在返回 False。"""
        allowed = {"title", "description", "due_at", "priority"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        if "title" in sets and not str(sets["title"]).strip():
            return False
        sets["updated_at"] = self._now()
        sql = "UPDATE assistant_task SET " + ",".join(f"{k}=?" for k in sets) + " WHERE task_id=?"
        with self._lock:
            cur = self._conn.execute(sql, (*sets.values(), task_id))
            self._conn.commit()
            return cur.rowcount > 0

    def set_status(self, task_id: str, status: str) -> bool:
        """状态变更（含删除=cancelled）。非法状态拒绝。

        重新打开（→ open）时清空 `notified_at`：否则一条已完成又重开的任务
        永远不会再提醒（水位还停在上一轮），这是「重新激活」应有的语义。
        """
        if status not in ("open", "done", "cancelled"):
            raise ValueError(f"非法任务状态：{status!r}")
        with self._lock:
            if status == "open":
                cur = self._conn.execute(
                    "UPDATE assistant_task SET status=?, updated_at=?, notified_at=NULL "
                    "WHERE task_id=?",
                    (status, self._now(), task_id),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE assistant_task SET status=?, updated_at=? WHERE task_id=?",
                    (status, self._now(), task_id),
                )
            self._conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        keys = row.keys()
        return Task(
            task_id=row["task_id"], title=row["title"],
            description=row["description"] or "", due_at=row["due_at"],
            priority=row["priority"], status=row["status"],
            notified_at=(row["notified_at"] if "notified_at" in keys else None),
        )

    # ----- 到期提醒（Phase 7 Scheduler） -----
    @staticmethod
    def _parse_due(value: Optional[str]) -> Optional[datetime]:
        """解析 due_at；非 ISO 或无法解析返回 None（调用方跳过，不误报到期）。"""
        if not value:
            return None
        try:
            due = datetime.fromisoformat(str(value))
        except (ValueError, TypeError):
            return None
        # 带时区的按本机时区换算后去掉 tzinfo，避免 aware/naive 比较抛 TypeError
        if due.tzinfo is not None:
            due = due.astimezone().replace(tzinfo=None)
        return due

    def claim_due(
        self, now: Optional[datetime] = None, *, limit: int = 10
    ) -> List[Task]:
        """原子地「取走到期的 open 任务」并打上 notified_at（at-most-once）。

        为什么在同一把锁里 select + update：若先查后标，两次 tick 可能同时
        看到同一条任务，用户会收到重复提醒。打标放在这里，进程崩溃最多是
        「这条没提醒到」，不会变成「无限重复提醒」——打扰的代价更高。

        返回的任务对象里 notified_at 仍是查询时的旧值（标记在返回前完成），
        调用方不需要它。
        """
        now = now or datetime.now()
        stamp = now.isoformat(timespec="seconds")
        claimed: List[Task] = []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM assistant_task WHERE status='open' AND due_at IS NOT NULL "
                "AND notified_at IS NULL ORDER BY due_at LIMIT ?",
                (max(1, limit) * 4,),
            ).fetchall()
            for row in rows:
                task = self._row_to_task(row)
                due = self._parse_due(task.due_at)
                if due is None or due > now:
                    continue
                self._conn.execute(
                    "UPDATE assistant_task SET notified_at=?, updated_at=? WHERE task_id=?",
                    (stamp, stamp, task.task_id),
                )
                claimed.append(task)
                if len(claimed) >= limit:
                    break
            self._conn.commit()
        return claimed
