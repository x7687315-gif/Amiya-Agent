"""Phase 2B 测试：三内置工具 + TaskRepository + schema v4 迁移。"""
import sqlite3

import pytest

from core.memory.store import SCHEMA_VERSION, SQLiteMemoryStore
from core.tasks.repository import TaskRepository
from core.tools import ToolExecutor, ToolRegistry
from core.tools.builtin import CalculatorTool, TaskDeleteTool, TaskTool, TimeTool


# ---------- schema v4 迁移 ----------

def test_schema_version_is_6():
    # v4 = 行动系统三表；v5 = 到期提醒水位（notified_at）；v6 = 研究会话表
    assert SCHEMA_VERSION == 6


def test_fresh_db_has_v4_tables(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "fresh.db"))
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    for table in ("agent_run", "agent_run_step", "assistant_task"):
        row = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        assert row is not None, table
    cols = [r["name"] for r in store._conn.execute(
        "PRAGMA table_info(assistant_task)").fetchall()]
    assert "notified_at" in cols  # v5
    store.close()


def test_v3_migration_preserves_old_data(tmp_path):
    """造 v3 库插旧数据，打开后断言零丢失 + 三表可用。"""
    db = str(tmp_path / "v3.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE conversation(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,
          session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL);
        CREATE TABLE memory(id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
          content TEXT NOT NULL, confidence INTEGER DEFAULT 3, importance INTEGER DEFAULT 3,
          hits INTEGER DEFAULT 0, source_msg_id INTEGER, created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL, embedding BLOB, decay_rate REAL DEFAULT 0.0,
          last_confirmed_at TEXT);
        CREATE TABLE user_profile(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE persona_state(id INTEGER PRIMARY KEY CHECK(id=1), trust INTEGER DEFAULT 70,
          companionship_seconds INTEGER DEFAULT 0, companionship_day TEXT DEFAULT '',
          emotion TEXT DEFAULT 'calm', total_turns INTEGER DEFAULT 0,
          last_extract_msg_id INTEGER DEFAULT 0, last_seen_at TEXT DEFAULT '');
        CREATE TABLE memory_blacklist(id INTEGER PRIMARY KEY AUTOINCREMENT, keyword TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL);
        CREATE TABLE memory_candidate(id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL,
          content TEXT NOT NULL, confidence INTEGER DEFAULT 3, importance INTEGER DEFAULT 3,
          reason TEXT, source_msg_id INTEGER, status TEXT NOT NULL DEFAULT 'pending',
          created_at TEXT NOT NULL, decided_at TEXT, memory_id INTEGER);
        PRAGMA user_version=3;
    """)
    conn.execute("INSERT INTO conversation(ts,session_id,role,content) VALUES ('2026-01-01','s1','user','老数据')")
    conn.execute("INSERT INTO memory(type,content,created_at,updated_at) VALUES ('fact','旧记忆','2026-01-01','2026-01-01')")
    conn.commit()
    conn.close()

    store = SQLiteMemoryStore(db)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store._conn.execute("SELECT content FROM conversation").fetchone()[0] == "老数据"
    assert store._conn.execute("SELECT content FROM memory").fetchone()[0] == "旧记忆"
    # 新表可写
    store._conn.execute("INSERT INTO assistant_task(task_id,title,created_at,updated_at) "
                        "VALUES ('t1','测试','now','now')")
    store._conn.commit()
    assert store._conn.execute("SELECT COUNT(*) FROM assistant_task").fetchone()[0] == 1
    store.close()


def test_backup_created_on_v3_migration(tmp_path):
    db = str(tmp_path / "bk.db")
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE conversation(id INTEGER PRIMARY KEY, ts TEXT, session_id TEXT, role TEXT, content TEXT);
        CREATE TABLE memory(id INTEGER PRIMARY KEY);
        CREATE TABLE user_profile(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
        CREATE TABLE persona_state(id INTEGER PRIMARY KEY, trust INTEGER);
        CREATE TABLE memory_blacklist(id INTEGER PRIMARY KEY, keyword TEXT, created_at TEXT);
        CREATE TABLE memory_candidate(id INTEGER PRIMARY KEY);
        PRAGMA user_version=3;
    """)
    conn.close()
    store = SQLiteMemoryStore(db)
    store.close()
    backups = list(tmp_path.glob("bk.db.bak.*"))
    assert len(backups) == 1, backups


# ---------- TaskRepository ----------

def test_task_repository_crud(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "task.db"))
    repo = TaskRepository(store)
    tid = repo.create("写周报", description="周五前", priority=3, due_at="2026-11-27T18:00")
    assert repo.get(tid).title == "写周报"
    assert len(repo.list()) == 1
    assert repo.update(tid, title="改标题")
    assert repo.get(tid).title == "改标题"
    assert repo.set_status(tid, "done")
    assert repo.get(tid).status == "done"
    assert len(repo.list(status="open")) == 0
    store.close()


def test_task_isolation_from_memory(tmp_path):
    """任务数据绝不写 memory/memory_candidate。"""
    store = SQLiteMemoryStore(str(tmp_path / "iso.db"))
    repo = TaskRepository(store)
    repo.create("隔离测试")
    assert store._conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM memory_candidate").fetchone()[0] == 0
    store.close()


# ---------- 内置工具 ----------

def test_time_tool():
    r = TimeTool().execute({})
    assert r.ok and "2026" in r.content


def test_time_tool_bad_timezone():
    r = TimeTool().execute({"timezone": "Not/AZone"})
    assert not r.ok and r.error_code == "invalid_args"


@pytest.mark.parametrize("expr,expected", [
    ("1+2*3", "7"),
    ("(1+2)*3", "9"),
    ("2**10", "1024"),
    ("sqrt(16)", "4"),
    ("round(3.14159, 2)", "3.14"),
])
def test_calculator_valid(expr, expected):
    r = CalculatorTool().execute({"expression": expr})
    assert r.ok and r.content == expected, (expr, r.content)


@pytest.mark.parametrize("expr", [
    "__import__('os').system('echo hack')",
    "open('/etc/passwd').read()",
    "1; import os",
    "().__class__",
    "2**101",
    "1/0",
])
def test_calculator_rejects_dangerous(expr):
    r = CalculatorTool().execute({"expression": expr})
    assert not r.ok, expr


def test_task_tool_via_executor(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "tt.db"))
    repo = TaskRepository(store)
    registry = ToolRegistry()
    registry.register(TaskTool(repo))
    ex = ToolExecutor(registry)
    r = ex.execute("c1", "manage_task", {"action": "create", "title": "测试任务"})
    assert r.ok and "task_id" in (r.data or {})
    tid = r.data["task_id"]
    r2 = ex.execute("c2", "manage_task", {"action": "complete", "task_id": tid})
    assert r2.ok
    assert repo.get(tid).status == "done"
    store.close()


def test_task_delete_requires_approval(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "td.db"))
    repo = TaskRepository(store)
    tid = repo.create("待删除")
    registry = ToolRegistry()
    registry.register(TaskDeleteTool(repo))
    ex = ToolExecutor(registry)
    # L2 无审批器 → 拒绝
    r = ex.execute("c1", "delete_task", {"task_id": tid})
    assert not r.ok and r.error_code == "permission_denied"
    assert repo.get(tid).status == "open"  # 未被删除
    store.close()
