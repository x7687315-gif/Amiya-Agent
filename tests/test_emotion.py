"""P2 · Emotion 支柱（当前情绪）测试。

覆盖：
1. 规则判定 detect_emotion（worried/happy/thinking/calm + 优先级 + 空文本兜底）。
2. emotion_block 渲染【当前情绪】。
3. Agent.reply 把 emotion_block 注入系统提示词（位于关系之后、行为之前——由
   test_prompt_builder.test_build_system_injection_order 兜底顺序）。
4. 情绪持久化到 persona_state.emotion（系统状态表）。
5. 边界：情绪**不污染 memory 表**（三层物理隔离）。
6. 降级：持久化失败 / memory=None 时不影响对话。
"""
import pytest

from core import emotion
from core.agent import Agent
from core.memory import MemoryManager, SQLiteMemoryStore
from core.persona import load_persona


class CaptureLLM:
    """捕获系统提示词，返回固定回复。"""

    def __init__(self, reply="好的，用户。"):
        self.reply = reply
        self.system = None

    def stream_chat(self, system, history):
        self.system = system
        yield self.reply


@pytest.fixture()
def store():
    s = SQLiteMemoryStore(":memory:")
    yield s
    s.close()


# ---------- 1. 规则判定 ----------


def test_detect_emotion_categories():
    assert emotion.detect_emotion("我最近压力好大，总是失眠，快崩溃了") == "worried"
    assert emotion.detect_emotion("太谢谢你了，我终于搞定啦，哈哈") == "happy"
    assert emotion.detect_emotion("你觉得我应该怎么规划这个项目？") == "thinking"
    assert emotion.detect_emotion("嗯，好的") == "calm"


def test_detect_emotion_priority_worried_first():
    # 同时含担忧与疑问词：担忧优先（共情先于解题）
    assert emotion.detect_emotion("我很焦虑，该怎么办？") == "worried"
    # 空文本 / 空白 → calm
    assert emotion.detect_emotion("") == "calm"
    assert emotion.detect_emotion("   ") == "calm"


def test_emotion_block_renders_label():
    block = emotion.emotion_block("worried")
    assert block.startswith("【当前情绪】")
    assert "担忧" in block
    # 不控语气、不破人格的约束提示
    assert "不要因此脱离你的身份" in block


# ---------- 2/3. 注入系统提示词 ----------


def test_emotion_block_injected_into_system(store):
    llm = CaptureLLM()
    agent = Agent(persona=load_persona(), llm=llm, memory=MemoryManager(store))
    "".join(agent.reply("我最近压力好大，快崩溃了"))
    assert llm.system is not None
    assert "【当前情绪】" in llm.system
    assert "担忧" in llm.system


def test_emotion_block_default_calm(store):
    llm = CaptureLLM()
    agent = Agent(persona=load_persona(), llm=llm, memory=MemoryManager(store))
    "".join(agent.reply("嗯，知道了"))
    assert "【当前情绪】" in llm.system
    assert "平静" in llm.system


# ---------- 4. 持久化到 persona_state ----------


def test_emotion_persisted_to_persona_state(store):
    agent = Agent(persona=load_persona(), llm=CaptureLLM(), memory=MemoryManager(store))
    "".join(agent.reply("我很焦虑，不知道怎么办"))
    state = store.load_state()
    assert state["emotion"] == "worried"
    # 情绪随新一轮更新
    "".join(agent.reply("谢谢你，我好多啦，太开心了"))
    assert store.load_state()["emotion"] == "happy"


# ---------- 5. 边界：不污染 memory 表 ----------


def test_emotion_does_not_pollute_memory_table(store):
    mgr = MemoryManager(store)
    agent = Agent(persona=load_persona(), llm=CaptureLLM(), memory=mgr)
    "".join(agent.reply("我最近压力好大，快崩溃了"))
    # 情绪进了系统状态表
    assert store.load_state()["emotion"] == "worried"
    # 但 memory 正表没有任何新增（情绪不是用户事实/经历）
    assert mgr.list_memories() == []
    # 对话表只有本轮的 user/assistant 两条，情绪不落对话表
    roles = [r["role"] for r in store.recent_messages(limit=10)]
    assert roles == ["user", "assistant"]


# ---------- 6. 降级 ----------


def test_emotion_persist_failure_degrades(store):
    class BrokenManager(MemoryManager):
        def save_state(self, **fields):  # 模拟 persona_state 写失败
            raise RuntimeError("disk full")

    agent = Agent(persona=load_persona(), llm=CaptureLLM(), memory=BrokenManager(store))
    out = "".join(agent.reply("我很焦虑"))  # 不应抛错
    assert out == "好的，用户。"


def test_emotion_without_memory_still_works():
    llm = CaptureLLM()
    agent = Agent(persona=load_persona(), llm=llm, memory=None)  # 纯内存模式
    out = "".join(agent.reply("我很焦虑"))
    assert out == "好的，用户。"
    assert "担忧" in llm.system  # 情绪块仍注入，只是不持久化
