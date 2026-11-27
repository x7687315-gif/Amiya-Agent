"""全链迁移测试：v1 → v6，数据不丢、可重复打开。

为什么单独一组：既有测试只覆盖"上一版 → 下一版"（v3→v4、v4→v5），
但从老用户机器上升上来的是 **v1**。只测增量无法证明"跳着升"是安全的。

造库方式：直接用 `_SCHEMA_V1` 建表并写入 v1 时代的数据，再把 user_version
设成 1 —— 这是最接近真实老库的形态（不是拿新库改版本号）。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.memory.store import SCHEMA_VERSION, SQLiteMemoryStore, _SCHEMA_V1


def _make_v1_db(path: Path, *, conversations: int = 3, memories: int = 2) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA_V1)
        conn.execute("PRAGMA user_version=1")
        for i in range(conversations):
            conn.execute(
                "INSERT INTO conversation(ts, session_id, role, content)"
                " VALUES(?,?,?,?)",
                (f"2026-01-0{i+1}T10:00:00+08:00", "s1",
                 "user" if i % 2 == 0 else "assistant", f"第{i}条"),
            )
        for i in range(memories):
            conn.execute(
                "INSERT INTO memory(type, content, confidence, importance,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                ("fact", f"旧记忆{i}", 4, 6,
                 "2026-01-01T00:00:00+08:00", "2026-01-01T00:00:00+08:00"),
            )
        conn.execute(
            "INSERT INTO user_profile(key, value, updated_at) VALUES('city','湛江','2026-01-01T00:00:00+08:00')")
        conn.commit()
    finally:
        conn.close()


def _tables(store: SQLiteMemoryStore) -> set[str]:
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def test_v1_upgrades_to_latest_and_keeps_data(tmp_path):
    db = tmp_path / "old.db"
    _make_v1_db(db)

    store = SQLiteMemoryStore(str(db))
    try:
        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION

        # 旧数据一条不少
        assert store._conn.execute("SELECT COUNT(*) FROM conversation").fetchone()[0] == 3
        assert store._conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0] == 2
        assert store._conn.execute(
            "SELECT value FROM user_profile WHERE key='city'").fetchone()[0] == "湛江"

        # 每一版新增的结构都在
        names = _tables(store)
        for t in ("memory_candidate", "agent_run", "agent_run_step",
                  "assistant_task", "research_session"):
            assert t in names, f"缺少表 {t}"

        cols = {r[1] for r in store._conn.execute("PRAGMA table_info(assistant_task)")}
        assert "notified_at" in cols
    finally:
        store.close()


def test_reopening_is_idempotent(tmp_path):
    db = tmp_path / "old.db"
    _make_v1_db(db)

    for _ in range(3):
        store = SQLiteMemoryStore(str(db))
        version = store._conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION
        assert store._conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0] == 2
        store.close()


def test_migration_makes_backup(tmp_path):
    """文件库升级前会备份；备份是保险，但必须真的生成。"""
    db = tmp_path / "old.db"
    _make_v1_db(db)
    store = SQLiteMemoryStore(str(db))
    store.close()
    backups = list(tmp_path.glob("*.bak*"))
    assert backups, "升级前未生成备份"


def test_new_db_starts_at_latest_without_migration_backup(tmp_path):
    db = tmp_path / "new.db"
    store = SQLiteMemoryStore(str(db))
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    store.close()
    assert not list(tmp_path.glob("*.bak*")), "全新库不应触发迁移备份"


def test_research_session_writable_after_upgrade(tmp_path):
    db = tmp_path / "old.db"
    _make_v1_db(db)
    store = SQLiteMemoryStore(str(db))
    try:
        from core.research_session import ResearchSessionRepository

        repo = ResearchSessionRepository(store)
        sess = repo.create("conv-legacy")
        repo.record_turn(
            sess.session_id, user_text="u",
            assistant_message={"role": "assistant", "content": "a",
                               "server_tool_calls": [{"index": 0, "calls": [{"id": "c1"}]}]},
        )
        ok, reason = repo.check(repo.get(sess.session_id))
        assert ok, reason
    finally:
        store.close()
