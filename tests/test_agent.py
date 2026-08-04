from core.agent import Agent
from core.llm_client import DeepSeekLLMClient
from core.persona import load_persona


class FakeLLM:
    def __init__(self, reply="用户，助手一直都在哦。"):
        self.reply = reply

    def stream_chat(self, system, history):
        yield self.reply


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


def test_deepseek_client_constructable():
    # 仅验证构造不依赖网络（实际请求在 stream_chat 中发生）
    c = DeepSeekLLMClient(api_key="sk-x", base_url="https://api.deepseek.com/v1")
    assert c.url == "https://api.deepseek.com/v1/chat/completions"
    assert c.model == "deepseek-chat"
