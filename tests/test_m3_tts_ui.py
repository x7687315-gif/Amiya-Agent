"""M3 验收测试：把 TTSService.synthesize() 接到聊天气泡（点击 🔊 朗读）。

覆盖验收表：
- 中文 / 英文 / 中英混合气泡都能朗读（走真实合成 + 播放，需 API；此处用 FakeTTS 验证逻辑）
- 历史气泡朗读的是「该气泡自己的文本」，而非全局最新回复
- API 暂停时点击不崩 UI（graceful fail，不发播放）
- 全局静音时不发任何 TTS 请求，且 🔊 按钮隐藏
- 流式输出过程不触发 TTS（_worker 路径只负责显示，不朗读）
"""
from __future__ import annotations

import struct
import wave
from dataclasses import dataclass

import flet as ft

from core.tts.models import AudioData, TTSResult
from core.tts.player import AudioPlayer
from ui.app import AssistantApp
from ui.components.chat_bubble import ChatBubble
from ui.components.chat_area import ChatArea
from ui.components.header import Header
from ui.components.speaker import MuteState

# 注：ft.Control.update 的离线桩由 tests/conftest.py 的 autouse fixture 提供，
# 此处不再做模块级替换（模块级替换会泄漏到同一 pytest 会话的其它测试模块）。

# 一段合法的最小 wav 字节（RIFF/WAVE），供 player / FakeTTS 复用
_WAV_BYTES = (
    b"RIFF\x24\x00\x00\x00WAVE"
    b"fmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00"
    b"data\x00\x00\x00\x00"
)


def _make_valid_wav_bytes(seconds: float = 0.1, rate: int = 16000) -> bytes:
    """用 wave 模块合成一段真实可播放的 wav 字节（保证 RIFF/WAVE 合法）。"""
    import io
    import math

    buf = io.BytesIO()
    wf = wave.open(buf, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(rate)
    n = int(rate * seconds)
    for i in range(n):
        s = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
        wf.writeframes(struct.pack("<h", s))
    wf.close()
    return buf.getvalue()


def _find_iconbutton(ctrl: ft.Control, icon_name=None) -> "ft.IconButton | None":
    """递归查找 IconButton（可选按 icon 名过滤）。"""
    found = []

    def _walk(c):
        if isinstance(c, ft.IconButton) and (icon_name is None or c.icon == icon_name):
            found.append(c)
            return
        controls = getattr(c, "controls", None)
        if isinstance(controls, list):
            for child in controls:
                if isinstance(child, ft.Control):
                    _walk(child)
        content = getattr(c, "content", None)
        if isinstance(content, ft.Control):
            _walk(content)

    _walk(ctrl)
    return found[0] if found else None


class _FakePersona:
    name = "助手"
    title = "本地"
    address = None


class _FakeTTS:
    """记录 synthesize 调用次数与最后一次文本，用于断言「是否发了请求」。"""

    def __init__(self, result: TTSResult | None = None):
        self.calls = 0
        self.last_text: str | None = None
        self._result = result

    def synthesize(self, text, voice="assistant", text_lang="zh") -> TTSResult:
        self.calls += 1
        self.last_text = text
        if self._result is not None:
            return self._result
        return TTSResult.ok(AudioData(data=_WAV_BYTES, text=text))


class _FakePlayer:
    def __init__(self):
        self.calls = 0
        self.last: AudioData | None = None

    def play(self, audio: AudioData) -> bool:
        self.calls += 1
        self.last = audio
        return True

    def wait_all(self) -> None:
        """app 在播放排队后会等待播完（忙碌态覆盖播放期）；替身无需等待。"""
        pass


# ---------------------------------------------------------------------------
# M3-A：播放器本身
# ---------------------------------------------------------------------------


def test_audio_player_plays_valid_wav():
    p = AudioPlayer()
    assert p.play_bytes(_make_valid_wav_bytes()) is True


def test_audio_player_rejects_invalid_bytes():
    p = AudioPlayer()
    assert p.play_bytes(b"not a wav") is False
    assert p.play_bytes(b"") is False
    # 缺 RIFF/WAVE 头
    assert p.play_bytes(b"RIFFxxxxXXXXdata") is False


def test_audio_player_play_audio_data():
    p = AudioPlayer()
    assert p.play(AudioData(data=_WAV_BYTES, text="x")) is True
    assert p.play(AudioData(data=b"", text="x")) is False


# ---------------------------------------------------------------------------
# M3-B：每个最终助手气泡挂 🔊，点它只读「这一句」
# ---------------------------------------------------------------------------


def test_speaker_button_reads_own_static_text():
    captured = []
    bubble = ChatBubble.assistant("这是第一句", on_speak=captured.append)
    btn = _find_iconbutton(bubble, ft.Icons.VOLUME_UP)
    assert btn is not None
    btn.on_click(None)  # 模拟点击
    assert captured == ["这是第一句"]


def test_speaker_button_reads_own_text_not_latest():
    c1, c2 = [], []
    b1 = ChatBubble.assistant("第一句", on_speak=c1.append)
    b2 = ChatBubble.assistant("第二句", on_speak=c2.append)
    _find_iconbutton(b1, ft.Icons.VOLUME_UP).on_click(None)
    _find_iconbutton(b2, ft.Icons.VOLUME_UP).on_click(None)
    assert c1 == ["第一句"]
    assert c2 == ["第二句"]


def test_speaker_button_reads_streaming_final_text():
    # 模拟流式：先建空 text_control，追加后最终值才是朗读内容
    captured = []
    tc = ft.Text("", selectable=True)
    bubble = ChatBubble.assistant("", text_control=tc, on_speak=captured.append)
    tc.value = "流式结束后的最终台词"
    _find_iconbutton(bubble, ft.Icons.VOLUME_UP).on_click(None)
    assert captured == ["流式结束后的最终台词"]


def test_no_speaker_when_on_speak_none():
    bubble = ChatBubble.assistant("没有朗读键")
    assert _find_iconbutton(bubble, ft.Icons.VOLUME_UP) is None


# ---------------------------------------------------------------------------
# M3-C：全局静音
# ---------------------------------------------------------------------------


def test_mute_state_default_unmuted():
    assert MuteState().muted is False


def test_speaker_hidden_when_muted():
    mute = MuteState(muted=True)
    bubble = ChatBubble.assistant("静音态的气泡", on_speak=lambda t: None, mute_state=mute)
    btn = _find_iconbutton(bubble, ft.Icons.VOLUME_UP)
    assert btn is not None
    assert btn.visible is False


def test_mute_toggle_notifies_subscribers():
    mute = MuteState()
    bubble = ChatBubble.assistant("订阅气泡", on_speak=lambda t: None, mute_state=mute)
    btn = _find_iconbutton(bubble, ft.Icons.VOLUME_UP)
    assert btn.visible is True
    mute.toggle()  # -> muted
    assert btn.visible is False
    mute.toggle()  # -> unmuted
    assert btn.visible is True


def test_header_mute_button_toggles_state_and_icon():
    mute = MuteState()
    header = Header(_FakePersona(), mute_state=mute)
    btn = _find_iconbutton(header, ft.Icons.VOLUME_UP) or _find_iconbutton(header, ft.Icons.VOLUME_OFF)
    assert btn is not None
    assert mute.muted is False
    assert btn.icon == ft.Icons.VOLUME_UP
    btn.on_click(None)  # 模拟点击静音
    assert mute.muted is True
    assert btn.icon == ft.Icons.VOLUME_OFF


# ---------------------------------------------------------------------------
# M3：speak 逻辑（真实 _speak 方法，注入 FakeTTS/FakePlayer）
# ---------------------------------------------------------------------------


def _app_with(fake_tts, fake_player, muted=False):
    app = AssistantApp()
    app.tts = fake_tts
    app.player = fake_player
    app.mute_state = MuteState(muted=muted)
    return app


def test_speak_unmuted_triggers_tts_and_play():
    tts, player = _FakeTTS(), _FakePlayer()
    app = _app_with(tts, player)
    app._speak("用户，欢迎回来。")
    assert tts.calls == 1 and tts.last_text == "用户，欢迎回来。"
    assert player.calls == 1


def test_speak_muted_sends_no_tts_request():
    tts, player = _FakeTTS(), _FakePlayer()
    app = _app_with(tts, player, muted=True)
    app._speak("用户，欢迎回来。")
    assert tts.calls == 0
    assert player.calls == 0


def test_speak_empty_text_no_request():
    tts, player = _FakeTTS(), _FakePlayer()
    app = _app_with(tts, player)
    app._speak("")
    app._speak("   ")
    assert tts.calls == 0


def test_speak_api_down_is_graceful_no_play():
    # TTS 返回失败：合成失败只降级，不调播放、不抛异常
    tts = _FakeTTS(result=TTSResult.fail("connection_refused"))
    player = _FakePlayer()
    app = _app_with(tts, player)
    app._speak("用户，你在忙吗？")  # 不应抛
    assert tts.calls == 1
    assert player.calls == 0


def test_speak_invalid_audio_no_play():
    # 合成「成功」但音频为空：同样不播放
    tts = _FakeTTS(result=TTSResult.ok(AudioData(data=b"", text="x")))
    player = _FakePlayer()
    app = _app_with(tts, player)
    app._speak("x")
    assert tts.calls == 1
    assert player.calls == 0


def test_speak_plays_directly_in_worker_thread():
    """合成成功后直接调用 player.play；AudioPlayer 内部自行处理线程，UI 层无需 run_task。"""
    tts, player = _FakeTTS(), _FakePlayer()
    app = _app_with(tts, player)
    app._speak("用户，欢迎回来。")
    assert tts.calls == 1
    assert player.calls == 1


# ---------------------------------------------------------------------------
# M3：流式路径绝不触发 TTS
# ---------------------------------------------------------------------------


def test_streaming_path_never_calls_speak():
    calls = []
    area = ChatArea(_FakePersona(), on_speak=calls.append, mute_state=MuteState())
    # 完整流式流程：start -> 多次 append -> finish
    tc = area.start_assistant()
    area.append_assistant_text(tc, "用户")
    area.append_assistant_text(tc, "，欢迎回来。")
    area.add_assistant("另一条完整回复")
    # 注意：start_assistant/add_assistant 只负责显示，点了 🔊 才朗读；此处未点击
    assert calls == []
    # 即使点了，也只朗读该气泡文本，与「全局最新」无关（结构性保证见上面的单测）
    btns = [_find_iconbutton(area._list.controls[-1], ft.Icons.VOLUME_UP)]
    # 最后一条 add_assistant 的气泡应挂 🔊
    assert btns[0] is not None
