"""Phase 7 测试：v5 迁移 + 到期领取（at-most-once）+ Scheduler + UI 提醒。"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from core.memory.store import SCHEMA_VERSION

from core.memory.store import SQLiteMemoryStore
from core.scheduler import TaskScheduler
from core.tasks import TaskRepository
from ui.app import AmiyaApp


# ---------------------------------------------------------------- v5 迁移

def test_fresh_db_is_v6_with_notified_at(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "a.db"))
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    cols = [r["name"] for r in store._conn.execute(
        "PRAGMA table_info(assistant_task)").fetchall()]
    assert "notified_at" in cols
    store.close()


def test_v4_upgrades_to_latest_without_data_loss(tmp_path):
    """手工造一个 v4 库（只有 v4 三表），打开后应补列且旧任务仍在。"""
    path = str(tmp_path / "v4.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE assistant_task (
      task_id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT,
      due_at TEXT, priority INTEGER DEFAULT 5, status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    INSERT INTO assistant_task(task_id,title,due_at,status,created_at,updated_at)
    VALUES ('t1','旧任务','2026-01-01T09:00:00','open','2026-01-01','2026-01-01');
    PRAGMA user_version=4;
    """)
    conn.commit()
    conn.close()

    store = SQLiteMemoryStore(path)
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    repo = TaskRepository(store)
    task = repo.get("t1")
    assert task is not None and task.title == "旧任务"
    assert task.notified_at is None
    store.close()


# ---------------------------------------------------------------- claim_due

def _repo(tmp_path) -> tuple[SQLiteMemoryStore, TaskRepository]:
    store = SQLiteMemoryStore(str(tmp_path / "t.db"))
    return store, TaskRepository(store)


def test_claim_due_only_past_open_unnotified(tmp_path):
    store, repo = _repo(tmp_path)
    now = datetime(2026, 11, 23, 21, 0, 0)
    past = repo.create("过期", due_at=(now - timedelta(minutes=5)).isoformat())
    future = repo.create("未到", due_at=(now + timedelta(hours=1)).isoformat())
    no_due = repo.create("无期限")
    done = repo.create("已完成", due_at=(now - timedelta(hours=1)).isoformat())
    repo.set_status(done, "done")

    claimed = repo.claim_due(now)

    assert [t.task_id for t in claimed] == [past]
    assert repo.get(past).notified_at is not None
    assert repo.get(future).notified_at is None
    assert repo.get(no_due).notified_at is None
    # 已完成的即使过期也不提醒
    assert repo.get(done).notified_at is None
    store.close()


def test_claim_due_is_at_most_once(tmp_path):
    store, repo = _repo(tmp_path)
    now = datetime(2026, 11, 23, 21, 0, 0)
    repo.create("过期", due_at=(now - timedelta(minutes=1)).isoformat())

    assert len(repo.claim_due(now)) == 1
    assert repo.claim_due(now) == []          # 第二次不得重复
    assert repo.claim_due(now + timedelta(days=1)) == []
    store.close()


def test_claim_due_skips_unparseable_and_handles_tz(tmp_path):
    store, repo = _repo(tmp_path)
    now = datetime(2026, 11, 23, 21, 0, 0)
    bad = repo.create("坏时间", due_at="下周五")
    # 带时区的过去时间：应能正常比较（aware/naive 不能直接比）
    aware = repo.create("带时区", due_at="2026-11-23T12:00:00+00:00")

    claimed = repo.claim_due(now)
    ids = [t.task_id for t in claimed]
    assert bad not in ids
    assert aware in ids
    store.close()


def test_reopening_task_rearms_reminder(tmp_path):
    """完成→重开应清空 notified_at，否则这条任务永远不会再提醒。"""
    store, repo = _repo(tmp_path)
    now = datetime(2026, 11, 23, 21, 0, 0)
    tid = repo.create("重开的任务", due_at=(now - timedelta(minutes=1)).isoformat())

    assert len(repo.claim_due(now)) == 1
    repo.set_status(tid, "done")
    repo.set_status(tid, "open")
    assert repo.get(tid).notified_at is None
    assert len(repo.claim_due(now)) == 1  # 重新提醒
    store.close()


def test_claim_due_respects_limit(tmp_path):
    store, repo = _repo(tmp_path)
    now = datetime(2026, 11, 23, 21, 0, 0)
    for i in range(5):
        repo.create(f"t{i}", due_at=(now - timedelta(minutes=i + 1)).isoformat())
    assert len(repo.claim_due(now, limit=2)) == 2
    store.close()


# ---------------------------------------------------------------- Scheduler

class _FakeRepo:
    def __init__(self, batches):
        self._batches = list(batches)

    def claim_due(self, now, *, limit=10):
        return self._batches.pop(0) if self._batches else []


def _task(task_id="t1", title="写周报"):
    return SimpleNamespace(task_id=task_id, title=title, due_at="2026-11-23T20:00:00")


def test_scheduler_tick_delivers_and_isolates_errors():
    got: list = []
    repo = _FakeRepo([[_task("a"), _task("b")]])

    def on_due(task):
        if task.task_id == "a":
            raise RuntimeError("回调炸了")
        got.append(task.task_id)

    sched = TaskScheduler(repo, on_due)
    assert [t.task_id for t in sched.tick()] == ["a", "b"]
    assert got == ["b"]  # 单条异常不影响其余任务


def test_scheduler_start_ticks_immediately_and_stops():
    got: list = []
    repo = _FakeRepo([[_task()]])
    sched = TaskScheduler(repo, got.append, interval_s=60)

    sched.start()
    try:
        assert sched.running is True
        # 先 tick 再等：启动即处理已过期任务（不阻塞测试，用轮询等回调）
        for _ in range(100):
            if got:
                break
            import time
            time.sleep(0.01)
    finally:
        sched.stop()

    assert [t.task_id for t in got] == ["t1"]
    assert sched.running is False


def test_scheduler_interval_is_clamped():
    sched = TaskScheduler(_FakeRepo([]), lambda t: None, interval_s=0)
    assert sched.interval_s == 1.0  # 防止 0 间隔空转烧 CPU


# ---------------------------------------------------------------- UI 提醒

def test_show_task_due_adds_notice_and_skips_autorun_by_default():
    notices: list[str] = []
    app = AmiyaApp()
    app.chat_area = SimpleNamespace(add_notice=notices.append)
    app._settings = SimpleNamespace(scheduler_autorun=False)
    autorun: list = []
    app._autorun_due_task = lambda task: autorun.append(task)

    asyncio.run(app._show_task_due(_task(title="周五前写完周报")))

    assert notices and "周五前写完周报" in notices[0]
    assert "到点了" in notices[0]
    assert autorun == []  # 默认不自动执行


def test_show_task_due_triggers_autorun_when_enabled():
    app = AmiyaApp()
    app.chat_area = SimpleNamespace(add_notice=lambda text: None)
    app._settings = SimpleNamespace(scheduler_autorun=True)
    autorun: list = []
    app._autorun_due_task = lambda task: autorun.append(task.task_id)

    asyncio.run(app._show_task_due(_task("x")))

    assert autorun == ["x"]


def test_autorun_skips_when_busy():
    app = AmiyaApp()
    app._runtime_stack = SimpleNamespace(router=SimpleNamespace(
        decide=lambda **kw: SimpleNamespace(path="runtime")))
    app._task_runner = SimpleNamespace(is_running=True)
    app._generating = False
    started: list = []
    app.page = SimpleNamespace(run_thread=lambda *a: started.append(a))

    app._autorun_due_task(_task())

    assert started == []  # 有任务在跑：本次只提醒，不排队


def test_autorun_uses_router_internal_task_signal():
    """internal_task 是 Router 的第三个信号，这里是它唯一的真实消费者。"""
    app = AmiyaApp()
    seen: list = []

    def _decide(**kw):
        seen.append(kw)
        return SimpleNamespace(path="runtime")

    app._runtime_stack = SimpleNamespace(router=SimpleNamespace(decide=_decide))
    app._task_runner = SimpleNamespace(is_running=False)
    app._generating = False
    app._begin_busy = lambda **_kw: None
    app.chat_area = SimpleNamespace(add_user=lambda *a, **k: None)
    app.page = SimpleNamespace(run_thread=lambda *a: None)

    app._autorun_due_task(_task())

    assert seen and seen[0]["task_source"] == "scheduler"


def test_autorun_respects_router_refusal():
    app = AmiyaApp()
    app._runtime_stack = SimpleNamespace(router=SimpleNamespace(
        decide=lambda **kw: SimpleNamespace(path="fast")))
    app._task_runner = SimpleNamespace(is_running=False)
    app._generating = False
    started: list = []
    app.page = SimpleNamespace(run_thread=lambda *a: started.append(a))

    app._autorun_due_task(_task())

    assert started == []
