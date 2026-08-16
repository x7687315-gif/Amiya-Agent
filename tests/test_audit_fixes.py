"""审计修复回归测试（2026-08-17，配套 docs/13_AUDIT_FIX_REPORT.md）。

覆盖：
- LLM 退避重试：零产出前连接类异常重试、已产出不重试、耗尽后上抛（流式 + 非流式）
- Agent.request_stop：停止后部分回复照常落库并入窗口；on_reply_complete 事件
- AudioPlayer.stop_all：清空队列 + 停止当前播放
- TTSStatusState / TTSStatusBanner：忙碌订阅、不可用提示
- ChatBubble 🔊 忙碌态（禁用 + 转圈图标）
- InputBar：loading 时发送键变停止键；未注入 on_stop 保持旧行为
- Header 新对话按钮
- config：TTS_VOICE / TTS_TEXT_LANG
- app._speak：忙碌态包裹、成功撤横幅、失败亮横幅、voice/lang 来自配置
"""
from __future__ import annotations

import time as _time
from typing import Iterator, List

import flet as ft
import pytest
import requests as _requests

from config import load_settings
from core.agent import Agent
from core.llm_client import DeepSeekLLMClient
from core.memory.manager import MemoryManager
from core.memory.store import SQLiteMemoryStore
from core.persona import load_persona
from core.tts.models import AudioData, TTSResult, TTSErrorType
from ui.app import AssistantApp
from ui.components.chat_bubble import ChatBubble
from ui.components.header import Header
from ui.components.input_bar import InputBar
from ui.components.speaker import MuteState
from ui.components.tts_status import TTSStatusBanner, TTSStatusState

# ---------------------------------------------------------------------------
# LLM 退避重试
# ---------------------------------------------------------------------------

_GOOD_LINES = [
    'data: {"choices":[{"delta":{"content":"你好"}}]}',
    "data: [DONE]",
]


class _StreamResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)


class _ChatResp:
    status_code = 200
    text = ""

    def json(self):
        return {"choices": [{"message": {"content": "好的"}}]}


def _llm() -> DeepSeekLLMClient:
    return DeepSeekLLMClient(api_key="k", base_url="http://x/v1")


def test_stream_chat_retries_transient_before_output(monkeypatch):
    calls = []
    monkeypatch.setattr(_time, "sleep", lambda _s: None)

    def fake_post(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise _requests.exceptions.ConnectionError("boom")
        return _StreamResp(_GOOD_LINES)

    monkeypatch.setattr("core.llm_client.requests.post", fake_post)
    out = "".join(_llm().stream_chat("sys", []))
    assert out == "你好"
    assert len(calls) == 3  # 失败×2 + 成功×1


def test_stream_chat_no_retry_after_output(monkeypatch):
    calls = []
    monkeypatch.setattr(_time, "sleep", lambda _s: None)

    def _gen():
        yield _GOOD_LINES[0]
        raise _requests.exceptions.Timeout("mid-stream")

    def fake_post(*a, **k):
        calls.append(1)
        return _StreamResp(_gen())

    monkeypatch.setattr("core.llm_client.requests.post", fake_post)
    with pytest.raises(_requests.exceptions.Timeout):
        list(_llm().stream_chat("sys", []))
    assert len(calls) == 1  # 已产出内容：绝不重试（会重复文本）


def test_stream_chat_raises_after_retries_exhausted(monkeypatch):
    calls = []
    monkeypatch.setattr(_time, "sleep", lambda _s: None)
    def always_down(*a, **k):
        calls.append(1)
        raise _requests.exceptions.ConnectionError("down")

    monkeypatch.setattr("core.llm_client.requests.post", always_down)
    with pytest.raises(_requests.exceptions.ConnectionError):
        list(_llm().stream_chat("sys", []))
    assert len(calls) == 3  # 1 + 2 次重试


def test_chat_retries_and_succeeds(monkeypatch):
    calls = []
    monkeypatch.setattr(_time, "sleep", lambda _s: None)

    def fake_post(*a, **k):
        calls.append(1)
        if len(calls) < 2:
            raise _requests.exceptions.Timeout("t")
        return _ChatResp()

    monkeypatch.setattr("core.llm_client.requests.post", fake_post)
    assert _llm().chat("sys", []) == "好的"
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# Agent.request_stop / on_reply_complete
# ---------------------------------------------------------------------------


class _SlowLLM:
    """慢速流式：逐字出，模拟生成中。"""

    def __init__(self):
        self.systems: List[str] = []

    def stream_chat(self, system: str, history) -> Iterator[str]:
        self.systems.append(system)
        for ch in "用户欢迎回来":
            yield ch


def test_agent_stop_persists_partial_reply():
    store = SQLiteMemoryStore(":memory:")
    completed: List[str] = []
    agent = Agent(
        persona=load_persona(),
        llm=_SlowLLM(),
        memory=MemoryManager(store),
        on_reply_complete=completed.append,
    )
    out = ""
    for delta in agent.reply("讲个长一点的故事"):
        out += delta
        if len(out) == 2:
            agent.request_stop()  # 收到第 2 个字后请求停止
    assert 0 < len(out) < 6  # 部分回复
    # 停止后收尾：部分回复落库 + 入短期窗口 + 触发完成事件
    assert agent._history[-1]["content"] == out
    assert any(m["content"] == out for m in store.recent_messages(10))
    assert completed == [out]
    # 标志被消费：下一轮回复不受影响
    assert agent._stop_requested is False


def test_agent_on_reply_complete_not_fired_on_empty():
    class _EmptyLLM:
        def stream_chat(self, system, history):
            return iter([])

    completed: List[str] = []
    agent = Agent(persona=load_persona(), llm=_EmptyLLM(), on_reply_complete=completed.append)
    list(agent.reply("你好"))
    assert completed == []  # 空回复不触发事件


# ---------------------------------------------------------------------------
# AudioPlayer.stop_all
# ---------------------------------------------------------------------------


def test_player_stop_all_clears_queue_and_stops_current(tmp_path, monkeypatch):
    import core.tts.player as pl

    played = []

    class _FakeWinsound:
        SND_FILENAME = 1

        def PlaySound(self, path, flags):
            played.append((path, flags))

    monkeypatch.setattr(pl, "winsound", _FakeWinsound())
    monkeypatch.setattr(pl, "_HAS_WINSOUND", True)
    monkeypatch.setattr(pl, "_SND_FILENAME", 1)

    p = pl.AudioPlayer(cache_dir=str(tmp_path))
    # 直接塞两条进队列（不启 worker，保证确定性）
    p._queue.put(p._write_temp(b"w1"))
    p._queue.put(p._write_temp(b"w2"))
    p.stop_all()
    assert p._queue.empty()  # 待播队列已清空
    assert (None, 0) in played  # 发起了「停止当前播放」


# ---------------------------------------------------------------------------
# TTSStatusState / TTSStatusBanner / ChatBubble 忙碌态
# ---------------------------------------------------------------------------


def test_tts_status_state_subscribe_unsubscribe():
    st = TTSStatusState()
    seen = []
    cb = seen.append
    st.subscribe(cb)
    assert seen == [False]  # 注册即对齐
    st.set_busy(True)
    assert seen == [False, True]
    st.unsubscribe(cb)
    st.set_busy(False)
    assert seen == [False, True]  # 退订后不再通知


def test_tts_banner_show_hide():
    b = TTSStatusBanner()
    assert b.visible is False
    b.show("语音服务暂不可用")
    assert b.visible is True
    assert "语音服务暂不可用" in b._text.value
    b.hide()
    assert b.visible is False


def test_bubble_speaker_busy_state():
    st = TTSStatusState()
    bubble = ChatBubble.assistant("文本", on_speak=lambda _t: None, tts_state=st)
    btn = None
    for ctrl in bubble.controls:
        if isinstance(ctrl, ft.IconButton):
            btn = ctrl
    assert btn is not None and btn.disabled is False
    st.set_busy(True)
    assert btn.disabled is True
    assert btn.icon == ft.Icons.HOURGLASS_TOP_ROUNDED
    st.set_busy(False)
    assert btn.disabled is False and btn.icon == ft.Icons.VOLUME_UP


# ---------------------------------------------------------------------------
# InputBar 停止键 / Header 新对话
# ---------------------------------------------------------------------------


def test_input_bar_stop_button(monkeypatch):
    stopped = []
    bar = InputBar(on_send=lambda _t: None, on_stop=lambda: stopped.append(1))
    bar.set_loading(True)
    assert bar._send_btn.icon == ft.Icons.STOP_CIRCLE_ROUNDED
    assert bar._send_btn.disabled is False
    bar._send_btn.on_click(None)  # 点停止
    assert len(stopped) == 1
    bar.set_loading(False)
    assert bar._send_btn.icon == ft.Icons.SEND_ROUNDED  # 恢复发送键


def test_input_bar_without_stop_keeps_old_behavior():
    bar = InputBar(on_send=lambda _t: None)
    bar.set_loading(True)
    assert isinstance(bar._send_btn.icon, ft.ProgressRing)
    assert bar._send_btn.disabled is True


class _HP:
    name = "助手"
    title = "本地"
    address = None


def test_header_new_chat_button():
    clicked = []
    h = Header(_HP(), on_new_chat=lambda: clicked.append(1))
    assert h._new_chat_btn.visible is True
    h._new_chat_btn.on_click(None)
    assert len(clicked) == 1
    # 未注入时按钮隐藏
    assert Header(_HP())._new_chat_btn.visible is False


# ---------------------------------------------------------------------------
# config：TTS_VOICE / TTS_TEXT_LANG
# ---------------------------------------------------------------------------


def test_settings_tts_voice_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("TTS_VOICE", "kelsey")
    monkeypatch.setenv("TTS_TEXT_LANG", "en")
    s = load_settings()
    assert s.tts_voice == "kelsey"
    assert s.tts_text_lang == "en"
    monkeypatch.delenv("TTS_VOICE")
    monkeypatch.delenv("TTS_TEXT_LANG")
    s2 = load_settings()
    assert s2.tts_voice == "assistant" and s2.tts_text_lang == "zh"  # 默认值


# ---------------------------------------------------------------------------
# app._speak：忙碌态 / 横幅 / voice 配置
# ---------------------------------------------------------------------------


class _TTS:
    def __init__(self, result=None, voice_lang=None):
        self.calls = []
        self._result = result
        self._voice_lang = voice_lang

    def synthesize(self, text, voice="assistant", text_lang="zh"):
        self.calls.append((text, voice, text_lang))
        if self._result is not None:
            return self._result
        return TTSResult.ok(AudioData(data=b"RIFFxxxxWAVEdata", text=text))


class _Player:
    def __init__(self):
        self.calls = 0
        self.waited = 0

    def play(self, audio):
        self.calls += 1
        return True

    def wait_all(self):
        self.waited += 1


def _app_with(tts, player):
    app = AssistantApp()
    app.tts = tts
    app.player = player
    app.mute_state = MuteState()
    app.tts_banner = TTSStatusBanner()
    return app


def test_speak_busy_wraps_and_uses_configured_voice():
    tts, player = _TTS(), _Player()
    app = _app_with(tts, player)
    app._tts_voice, app._tts_lang = "kelsey", "en"
    busy_seen = []
    app.tts_state.subscribe(busy_seen.append)
    app._speak("Hello")
    assert tts.calls == [("Hello", "kelsey", "en")]  # voice/lang 来自配置
    assert player.calls == 1 and player.waited == 1  # 排队并等到播完
    assert busy_seen == [False, True, False]  # 忙碌态包裹全程
    assert app.tts_banner.visible is False  # 成功不亮横幅


def test_speak_failure_shows_banner_and_clears_busy():
    tts = _TTS(result=TTSResult.fail("refused", TTSErrorType.CONNECTION))
    player = _Player()
    app = _app_with(tts, player)
    app._speak("你好")
    assert player.calls == 0
    assert app.tts_banner.visible is True  # 用户可见提示（不再只写日志）
    assert app.tts_state.busy is False
