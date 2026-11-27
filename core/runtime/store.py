"""RunRepository：agent_run / agent_run_step 的持久化门面。

契约：DATA_MODEL_2.0.md §3.1/§3.2。
- 复用 MemoryStore 连接与 RLock（单连接 WAL）。
- Tool Call 不单独建表：统一进 agent_run_step（step_type 区分）。
- "模型可见 ⟺ 已记录"：每次模型请求/工具执行都落 step。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from core.memory.store import SQLiteMemoryStore

from .state import RunStatus


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    session_id: str
    status: RunStatus
    trigger: str
    created_at: str
    budget_steps: int = 5


@dataclass(frozen=True)
class StepRecord:
    step_id: int
    run_id: str
    sequence: int
    step_type: str
    status: str
    tool_name: Optional[str] = None
    tool_call_id: Optional[str] = None


class RunRepository:
    """Run/Step 仓储。共享 store 的连接与锁。"""

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._conn = store._conn
        self._lock = store._lock

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def create_run(
        self, session_id: str, *, trigger: str = "chat", handoff_from: str | None = None,
        budget_steps: int = 5,
    ) -> str:
        run_id = uuid.uuid4().hex
        with self._lock:
            self._conn.execute(
                "INSERT INTO agent_run(run_id,session_id,status,trigger,handoff_from,"
                "created_at,budget_steps) VALUES (?,?,?,?,?,?,?)",
                (run_id, session_id, RunStatus.CREATED.value, trigger,
                 handoff_from, self._now(), budget_steps),
            )
            self._conn.commit()
        return run_id

    def start_run(self, run_id: str) -> None:
        self.set_status(run_id, RunStatus.RUNNING, started_at=self._now())

    def set_status(
        self, run_id: str, status: RunStatus, *, started_at: str | None = None,
        completed_at: str | None = None, final_response: str | None = None,
        error: str | None = None, total_tokens: int | None = None,
    ) -> bool:
        fields: dict = {"status": status.value}
        if started_at is not None:
            fields["started_at"] = started_at
        if completed_at is not None:
            fields["completed_at"] = completed_at
        if final_response is not None:
            fields["final_response"] = final_response
        if error is not None:
            fields["error"] = error
        if total_tokens is not None:
            fields["total_tokens"] = total_tokens
        sql = "UPDATE agent_run SET " + ",".join(f"{k}=?" for k in fields) + " WHERE run_id=?"
        with self._lock:
            cur = self._conn.execute(sql, (*fields.values(), run_id))
            self._conn.commit()
            return cur.rowcount > 0

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT run_id,session_id,status,trigger,created_at,budget_steps "
                "FROM agent_run WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            return None
        return RunRecord(
            run_id=row["run_id"], session_id=row["session_id"],
            status=RunStatus(row["status"]), trigger=row["trigger"],
            created_at=row["created_at"], budget_steps=row["budget_steps"],
        )

    def add_step(
        self,
        run_id: str,
        sequence: int,
        step_type: str,
        status: str,
        *,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        arguments: dict | None = None,
        result_summary: str | None = None,
        error_code: str | None = None,
        approval_outcome: str | None = None,
    ) -> int:
        """追加一步；UNIQUE(run_id,sequence) 保证步序唯一。arguments 存 JSON 原文。"""
        started = self._now() if status in ("running", "ok", "failed", "rejected", "cancelled") else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO agent_run_step(run_id,sequence,step_type,status,tool_name,"
                "tool_call_id,arguments_json,result_summary,error_code,approval_outcome,started_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, sequence, step_type, status, tool_name, tool_call_id,
                 json.dumps(arguments, ensure_ascii=False) if arguments is not None else None,
                 result_summary[:4000] if result_summary else None,
                 error_code, approval_outcome, started),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def finish_step(
        self, step_id: int, status: str, *, result_summary: str | None = None,
        error_code: str | None = None, approval_outcome: str | None = None,
    ) -> None:
        fields = {"status": status, "completed_at": self._now()}
        if result_summary is not None:
            fields["result_summary"] = result_summary[:4000]
        if error_code is not None:
            fields["error_code"] = error_code
        if approval_outcome is not None:
            fields["approval_outcome"] = approval_outcome
        sql = "UPDATE agent_run_step SET " + ",".join(f"{k}=?" for k in fields) + " WHERE step_id=?"
        with self._lock:
            self._conn.execute(sql, (*fields.values(), step_id))
            self._conn.commit()

    def list_steps(self, run_id: str) -> List[StepRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT step_id,run_id,sequence,step_type,status,tool_name,tool_call_id "
                "FROM agent_run_step WHERE run_id=? ORDER BY sequence", (run_id,)).fetchall()
        return [StepRecord(
            step_id=r["step_id"], run_id=r["run_id"], sequence=r["sequence"],
            step_type=r["step_type"], status=r["status"],
            tool_name=r["tool_name"], tool_call_id=r["tool_call_id"],
        ) for r in rows]
