import pytest

from core.agent import Agent
from core.knowledge.manager import KnowledgeManager
from core.llm_client import DeepSeekLLMClient
from core.memory import MemoryManager, SQLiteMemoryStore
from core.memory.embedder import HashingEmbedder
from core.persona import load_persona


class FakeLLM:
    def __init__(self, reply="用户，助手一直都在哦。"):
        self.reply = reply

    def stream_chat(self, system, history):
        yield self.reply


class BoomLLM:
    """模拟网络中断：用户消息已落库，但没有助手回复。"""

    def stream_chat(self, system, history):
        raise RuntimeError("network down")
        yield ""  # pragma: no cover


@pytest.fixture()
def store():
    s = SQLiteMemoryStore(":memory:")
    yield s
    s.close()


def test_reply_streams_and_updates_history():
    agent = Agent(persona=load_persona(), llm=FakeLLM(), history_limit=20)
    out = "".join(agent.reply("你好"))
    assert out == "用户，助手一直都在哦。"
    assert agent._history[0]["role"] == "user"
    assert agent._history[-1]["role"] == "assistant"
    assert agent._history[-1]["content"] == out


def test_history_window_truncates():
    agent = Agent(persona=load_persona(), llm=FakeLLM(), history_limit=3)
    for i in range(10):
        "".join(agent.reply(f"第{i}句"))
    # 窗口最多保留 history_limit * 2 条
    assert len(agent._history) == 6


def test_reset_clears_history():
    agent = Agent(persona=load_persona(), llm=FakeLLM())
    "".join(agent.reply("hi"))
    agent.reset()
    assert agent._history == []


# ----- Phase 2-A Step 2.2：记忆闭环 -----


def test_reply_persists_both_turns(store):
    agent = Agent(
        persona=load_persona(), llm=FakeLLM(), memory=MemoryManager(store)
    )
    "".join(agent.reply("你好"))
    rows = store.recent_messages(limit=10)
    assert [r["role"] for r in rows] == ["user", "assistant"]
    assert rows[1]["content"] == "用户，助手一直都在哦。"
    assert store.load_state()["total_turns"] == 1
    assert agent.memory_degraded is False


def test_cold_start_restores_history(store):
    first = Agent(persona=load_persona(), llm=FakeLLM(), memory=MemoryManager(store))
    "".join(first.reply("我叫用户"))

    # 模拟重启：新 Agent、新 Manager，同一个库
    second = Agent(persona=load_persona(), llm=FakeLLM(), memory=MemoryManager(store))
    assert [m["content"] for m in second._history] == [
        "我叫用户",
        "用户，助手一直都在哦。",
    ]


def test_restore_history_can_be_disabled(store):
    warm = Agent(persona=load_persona(), llm=FakeLLM(), memory=MemoryManager(store))
    "".join(warm.reply("先写一条"))
    fresh = Agent(
        persona=load_persona(),
        llm=FakeLLM(),
        memory=MemoryManager(store),
        restore_history=False,
    )
    assert fresh._history == []


def test_reset_rotates_session_but_keeps_db(store):
    mgr = MemoryManager(store)
    agent = Agent(persona=load_persona(), llm=FakeLLM(), memory=mgr)
    "".join(agent.reply("你好"))
    old_session = mgr.session_id

    agent.reset()

    assert agent._history == []
    assert mgr.session_id != old_session
    assert len(store.recent_messages(limit=10)) == 2  # 绝不删库


def test_user_message_survives_llm_failure(store):
    agent = Agent(persona=load_persona(), llm=BoomLLM(), memory=MemoryManager(store))
    with pytest.raises(RuntimeError):
        "".join(agent.reply("这句话不能丢"))
    rows = store.recent_messages(limit=10)
    assert [r["role"] for r in rows] == ["user"]
    assert rows[0]["content"] == "这句话不能丢"


class BrokenManager(MemoryManager):
    """模拟磁盘写满：记忆层每次写入都炸。"""

    def add_turn(self, role, content):
        raise RuntimeError("disk full")


def test_memory_failure_degrades_without_breaking_chat(store):
    agent = Agent(
        persona=load_persona(), llm=FakeLLM(), memory=BrokenManager(store)
    )
    out = "".join(agent.reply("你好"))
    assert out == "用户，助手一直都在哦。"  # 对话不受影响
    assert agent.memory_degraded is True


def test_agent_without_memory_writes_nothing(store):
    agent = Agent(persona=load_persona(), llm=FakeLLM())  # memory=None
    "".join(agent.reply("你好"))
    assert store.recent_messages(limit=10) == []


def test_deepseek_client_constructable():
    # 仅验证构造不依赖网络（实际请求在 stream_chat 中发生）
    c = DeepSeekLLMClient(api_key="sk-x", base_url="https://api.deepseek.com/v1")
    assert c.url == "https://api.deepseek.com/v1/chat/completions"
    assert c.model == "deepseek-chat"


# ----- Step 2.4：记忆检索 + Prompt 注入闭环 -----


class CaptureLLM:
    """记录收到的 system 提示词，不真正请求网络。"""

    def __init__(self, reply="用户，我记得哦。"):
        self.reply = reply
        self.last_system = ""

    def stream_chat(self, system, history):
        self.last_system = system
        yield self.reply


def test_agent_injects_memory_into_prompt(store):
    mgr = MemoryManager(store, embedder=HashingEmbedder(dim=128))
    mgr.remember("fact", "用户养了一只橘猫", importance=8, confidence=5)
    mgr.reindex()

    captured = []
    llm = CaptureLLM()
    agent = Agent(
        persona=load_persona(),
        llm=llm,
        memory=mgr,
        memory_top_k=3,
        on_retrieval=lambda hs: captured.extend(hs),
    )
    "".join(agent.reply("我的猫怎么样了"))

    # 1) Agent 真的去检索了
    assert captured, "Agent 应当检索到记忆"
    # 2) 检索命中被注入发给 LLM 的 system 提示词（定界、无分数）
    assert "【相关用户记忆】" in llm.last_system
    assert "橘猫" in llm.last_system
    assert "score=" not in llm.last_system


def test_agent_retrieval_failure_does_not_break_chat(store):
    class BrokenMemory(MemoryManager):
        def retrieve(self, query, *, top_k=5):
            raise RuntimeError("retrieval down")

    mgr = BrokenMemory(store)
    agent = Agent(persona=load_persona(), llm=CaptureLLM(), memory=mgr)
    out = "".join(agent.reply("你好"))
    assert out == "用户，我记得哦。"  # 检索失败只降级，对话照常


# ----- Step 2.7：Memory Retrieval 正式接入 Agent（Top-K 动态检索，三柱隔离）-----


def test_agent_skips_injection_without_relevant_memory(store):
    """无相关记忆 → 不注入记忆块；只做 Top-K 动态检索，绝不灌全部长期记忆。"""
    mgr = MemoryManager(store, embedder=HashingEmbedder(dim=128))
    # 记忆表为空：没有可检索的相关记忆
    llm = CaptureLLM()
    agent = Agent(persona=load_persona(), llm=llm, memory=mgr, memory_top_k=3)
    "".join(agent.reply("今天天气怎么样"))

    assert "【相关用户记忆】" not in llm.last_system
    # 关键：未把任何长期记忆堆进 system 提示词（Top-K 动态检索，非空库时也如此）
    assert mgr.list_memories() == []


def test_knowledge_does_not_pollute_memory(store, tmp_path):
    """Knowledge 三柱隔离：知识命中注入【角色知识】，绝不写入/注入【相关用户记忆】。"""
    kdir = tmp_path / "kb"
    kdir.mkdir()
    (kdir / "assistant.md").write_text(
        "# 技艺\n助手能操控技艺进行攻击。", encoding="utf-8"
    )
    embedder = HashingEmbedder(dim=128)
    kmgr = KnowledgeManager(kdir, embedder, top_k=3)

    mgr = MemoryManager(store, embedder=embedder)  # 记忆表初始为空
    captured = []
    llm = CaptureLLM()
    agent = Agent(
        persona=load_persona(),
        llm=llm,
        memory=mgr,
        knowledge=kmgr,
        on_retrieval=lambda hs: captured.extend(hs),
    )
    "".join(agent.reply("助手的技艺怎么样"))

    # 知识命中正确注入独立的【角色知识】块
    assert "【角色知识】" in llm.last_system
    assert "技艺" in llm.last_system
    # 记忆块不应出现（知识检索不做记忆副作用）
    assert "【相关用户记忆】" not in llm.last_system
    # 最关键：知识检索绝不能写入记忆表
    assert mgr.list_memories() == []
    # 记忆检索回调也不应被知识命中触发
    assert captured == []


def test_memory_does_not_pollute_knowledge(store, tmp_path):
    """Memory 三柱隔离：记忆命中注入【相关用户记忆】，绝不泄漏进【角色知识】。"""
    embedder = HashingEmbedder(dim=128)
    mgr = MemoryManager(store, embedder=embedder)
    mgr.remember("fact", "用户养了一只橘猫", importance=8, confidence=5)
    mgr.reindex()

    kdir = tmp_path / "kb"
    kdir.mkdir()  # 空知识库
    kmgr = KnowledgeManager(kdir, embedder, top_k=3)

    llm = CaptureLLM()
    agent = Agent(persona=load_persona(), llm=llm, memory=mgr, knowledge=kmgr)
    "".join(agent.reply("我的橘猫还好吗"))

    # 记忆命中正确注入独立的【相关用户记忆】块
    assert "【相关用户记忆】" in llm.last_system
    assert "橘猫" in llm.last_system
    # 知识块不应出现（知识库为空，且记忆不泄漏进知识）
    assert "【角色知识】" not in llm.last_system
    # 知识检索本身也不应返回记忆内容
    assert kmgr.retrieve("橘猫") == []

