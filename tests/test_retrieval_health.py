"""检索健康自检测试（2026-11-27 向量路静默失效的回归锁）。"""
from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace

from core.knowledge.loader import KnowledgeCorpus
from core.knowledge.manager import KnowledgeManager
from core.memory.embedder import HashingEmbedder, probe
from core.memory.manager import MemoryManager
from core.memory.store import SQLiteMemoryStore
from ui.app import AmiyaApp


class _BrokenEmbedder:
    name = "broken"
    dim = 2

    def encode(self, texts):
        raise RuntimeError("cannot import name 'is_offline_mode'")


class _EmptyEmbedder:
    name = "empty"
    dim = 2

    def encode(self, texts):
        return [[] for _ in texts]


# ---------------------------------------------------------------- probe

def test_probe_ok_and_broken():
    assert probe(HashingEmbedder()) is None
    assert "is_offline_mode" in probe(_BrokenEmbedder())
    assert probe(_EmptyEmbedder()) == "嵌入返回空向量"


# ---------------------------------------------------------------- managers

def test_memory_health_check_reports_broken_embedder(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "m.db"))
    mm = MemoryManager(store, embedder=_BrokenEmbedder())
    assert "is_offline_mode" in mm.health_check()
    store.close()


def test_memory_health_check_none_without_embedder(tmp_path):
    store = SQLiteMemoryStore(str(tmp_path / "m.db"))
    assert MemoryManager(store)._embedder is None
    assert MemoryManager(store).health_check() is None
    store.close()


def test_knowledge_health_check(tmp_path):
    (tmp_path / "a.md").write_text("# 标题\n正文\n", encoding="utf-8")
    corpus = KnowledgeCorpus(tmp_path)
    km = KnowledgeManager(tmp_path, _BrokenEmbedder())
    assert km.health_check() and "is_offline_mode" in km.health_check()
    # 健康时返回 None
    ok = KnowledgeManager(tmp_path, HashingEmbedder())
    assert ok.health_check() is None


# ---------------------------------------------------------------- app

def test_probe_retrieval_posts_notice_on_failure():
    app = AmiyaApp()
    app._health_checks = [("记忆", lambda: "RuntimeError: boom")]
    shown: list[str] = []
    app.page = SimpleNamespace(run_task=lambda fn, arg: shown.append(arg))

    app._probe_retrieval()

    assert shown and "记忆" in shown[0] and "boom" in shown[0]


def test_probe_retrieval_silent_when_healthy():
    app = AmiyaApp()
    app._health_checks = [("记忆", lambda: None), ("知识", lambda: None)]
    shown: list[str] = []
    app.page = SimpleNamespace(run_task=lambda fn, arg: shown.append(arg))

    app._probe_retrieval()

    assert shown == []


def test_probe_treats_exception_as_problem():
    app = AmiyaApp()

    def boom():
        raise OSError("model dir missing")

    app._health_checks = [("知识", boom)]
    shown: list[str] = []
    app.page = SimpleNamespace(run_task=lambda fn, arg: shown.append(arg))

    app._probe_retrieval()

    assert shown and "model dir missing" in shown[0]


def test_retrieval_notice_only_once():
    app = AmiyaApp()
    notices: list[str] = []
    app.chat_area = SimpleNamespace(add_notice=notices.append)

    asyncio.run(app._show_retrieval_issue("RuntimeError: boom"))
    asyncio.run(app._show_retrieval_issue("RuntimeError: boom"))

    assert len(notices) == 1
    assert "检索不可用" in notices[0]
