"""时间轴功能测试：时间感知提示词 + 按日期浏览 + 三个月滚动清理。

对应需求：
1. Agent 接入本机时间（每轮 system 提示词含「当前时间」块）
2. 对话按日期分类，UI 可按天回放（store/messages_on_day + ChatArea.show_day + DateNav）
3. 滚动窗口：保留最近 3 个自然月（到 4 月删 1 月），只清 conversation 表
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Iterator, List

import flet as ft

from core.agent import Agent
from core.memory.manager import MemoryManager
from core.memory.store import SQLiteMemoryStore
from core.persona import load_persona
from core.prompt_builder import PromptBuilder
from ui.components.chat_area import ChatArea
from ui.components.date_nav import DateNav, format_day
from ui.components.empty_state import EmptyState


# ---------------------------------------------------------------------------
# store：按天查询 / 日期列表 / 滚动删除
# ---------------------------------------------------------------------------


def _insert(store: SQLiteMemoryStore, ts: str, role: str = "user", content: str = "x") -> None:
    with store._lock:
        store._conn.execute(
            "INSERT INTO conversation(ts, session_id, role, content) VALUES(?,?,?,?)",
            (ts, "s-test", role, content),
        )
        store._conn.commit()


def test_messages_on_day_filters_and_orders():
    s = SQLiteMemoryStore(":memory:")
    _insert(s, "2026-08-15T23:00:00", content="昨天晚上")
    _insert(s, "2026-08-16T09:00:00", content="早上好")
    _insert(s, "2026-08-16T10:30:00", role="assistant", content="用户早")
    rows = s.messages_on_day("2026-08-16")
    assert [r["content"] for r in rows] == ["早上好", "用户早"]
    assert rows[0]["ts"].startswith("2026-08-16")


def test_days_with_messages_distinct_desc():
    s = SQLiteMemoryStore(":memory:")
    for ts in ("2026-08-16T09:00:00", "2026-08-16T10:00:00", "2026-08-01T09:00:00"):
        _insert(s, ts)
    assert s.days_with_messages() == ["2026-08-16", "2026-08-01"]


def test_purge_before_only_deletes_older():
    s = SQLiteMemoryStore(":memory:")
    _insert(s, "2026-01-31T23:00:00", content="一月")
    _insert(s, "2026-02-01T00:00:00", content="二月")  # 边界日当天保留
    deleted = s.purge_before("2026-02-01")
    assert deleted == 1
    assert [r["content"] for r in s.messages_on_day("2026-02-01")] == ["二月"]


# ---------------------------------------------------------------------------
# manager：三个月滚动窗口（到 4 月删 1 月）
# ---------------------------------------------------------------------------


def _mgr_with_rows(months: List[str]) -> MemoryManager:
    s = SQLiteMemoryStore(":memory:")
    for m in months:
        _insert(s, f"2026-{m}-15T12:00:00", content=f"{m}月")
    return MemoryManager(s)


def test_purge_keeps_three_calendar_months():
    m = _mgr_with_rows(["01", "02", "03", "04"])
    deleted = m.purge_old_conversations(keep_months=3, today=date(2026, 4, 15))
    assert deleted == 1  # 到 4 月删 1 月
    assert m.messages_on_day("2026-01-15") == []
    assert m.messages_on_day("2026-02-15") != []
    assert m.messages_on_day("2026-04-15") != []


def test_purge_window_crosses_year():
    s = SQLiteMemoryStore(":memory:")
    _insert(s, "2025-11-15T12:00:00", content="去年11月")
    _insert(s, "2025-12-15T12:00:00", content="去年12月")
    _insert(s, "2026-02-15T12:00:00", content="今年2月")
    m = MemoryManager(s)
    deleted = m.purge_old_conversations(keep_months=3, today=date(2026, 2, 5))
    assert deleted == 1  # 窗口 = 2025-12 起，11 月被删
    assert m.messages_on_day("2025-12-15") != []
    assert m.messages_on_day("2025-11-15") == []


def test_purge_keeps_one_month_only():
    m = _mgr_with_rows(["01", "02", "03", "04"])
    deleted = m.purge_old_conversations(keep_months=1, today=date(2026, 4, 15))
    assert deleted == 3  # 只留 4 月


# ---------------------------------------------------------------------------
# 时间感知：PromptBuilder / Agent
# ---------------------------------------------------------------------------


def test_prompt_builder_time_block():
    p = load_persona()
    assert "【当前时间】" not in PromptBuilder(p).build_system()  # 不传 now 不注入
    sys_p = PromptBuilder(p).build_system(now=datetime(2026, 8, 16, 14, 30))
    assert "【当前时间】" in sys_p
    assert "2026年8月16日" in sys_p
    assert "星期日" in sys_p
    assert "14:30" in sys_p


class _SpyLLM:
    """捕获 system 提示词，回一段固定文本。"""

    def __init__(self) -> None:
        self.systems: List[str] = []

    def stream_chat(self, system: str, messages) -> Iterator[str]:
        self.systems.append(system)
        yield "好的用户。"


def test_agent_injects_realtime_clock():
    agent = Agent(persona=load_persona(), llm=_SpyLLM())
    reply = "".join(agent.reply("现在几点了？"))
    assert reply == "好的用户。"
    assert "【当前时间】" in agent.llm.systems[0]  # 每轮重建，带实时时钟


# ---------------------------------------------------------------------------
# UI：DateNav / ChatArea.show_day
# ---------------------------------------------------------------------------


def test_format_day():
    assert format_day("2026-08-16") == "2026年8月16日 · 周日"
    assert format_day("bad-day") == "bad-day"


def _fire(btn: ft.IconButton):
    btn.on_click(None)


def test_date_nav_navigation_flow():
    picked = []
    nav = DateNav(on_day_change=picked.append, today="2026-08-16")
    nav.set_days(["2026-08-16", "2026-08-15", "2026-08-08"])

    _fire(nav._prev_btn)  # 新→旧：上一天
    assert picked == ["2026-08-15"]
    assert nav._today_btn.visible is True  # 非 today 显示「回到今天」
    assert nav._next_btn.disabled is False

    nav._today_btn.on_click(None)
    assert picked == ["2026-08-15", "2026-08-16"]
    assert nav._today_btn.visible is False

    nav.set_current("2026-08-08")  # 最旧一天：prev 应禁用
    assert nav._prev_btn.disabled is True
    nav.set_days(["2026-08-16"])  # 只有今天：next 也应禁用
    assert nav._next_btn.disabled is True


def _texts(ctrl: ft.Control) -> List[str]:
    found: List[str] = []

    def _walk(c):
        if isinstance(c, ft.Text):
            found.append(c.value or "")
        for child in getattr(c, "controls", None) or []:
            _walk(child)
        content = getattr(c, "content", None)
        if isinstance(content, ft.Control):
            _walk(content)

    _walk(ctrl)
    return found


class _FakePersona:
    name = "助手"
    title = "本地"
    address = None


def test_chat_area_show_day_replay_and_readonly():
    area = ChatArea(_FakePersona())
    msgs = [
        {"role": "user", "content": "早上好"},
        {"role": "assistant", "content": "用户早"},
    ]
    area.show_day("2026-08-15", msgs, today="2026-08-16")
    texts = [t for c in area._list.controls for t in _texts(c)]
    assert any("只读" in t for t in texts)      # 只读横幅
    assert "早上好" in texts and "用户早" in texts  # 按天回放

    area.show_day("2026-08-15", [], today="2026-08-16")  # 空的过去日
    texts = [t for c in area._list.controls for t in _texts(c)]
    assert any("没有对话记录" in t for t in texts)

    area.show_day("2026-08-16", [], today="2026-08-16")  # 今天没聊 → 欢迎空态
    assert any(isinstance(c, EmptyState) for c in area._list.controls)
