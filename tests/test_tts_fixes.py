"""审查修复的回归测试（2026-08-16，配套 docs/11_TTS_REVIEW_FIX_REPORT.md）。

覆盖：
- 共享模块 audio_utils：8-bit 拒绝、分块参数一致性校验
- TTSService：短文本发清洗后的文本、纯标点不发请求、sample_rate 取真实值、
  分块参数不一致时降级为失败结果（而不是拼出坏音频）
- AudioPlayer：FIFO 排队串行播放（连点不互相截断）、临时文件清理
- MuteState unsubscribe + ChatBubble/ChatArea 清理订阅（防泄漏）
- AssistantApp：run() 前属性有默认值；合成期间被静音则不播
- tts_web 与 audio_utils 共享同一实现（防漂移回归）
"""
from __future__ import annotations

import io
import math
import struct
import time
import wave
from pathlib import Path

import flet as ft
import pytest

import core.tts.player as player_module
from core.tts import audio_utils
from core.tts.models import AudioData, TTSResult, TTSErrorType
from core.tts.service import TTSService
from ui.app import AssistantApp
from ui.components.chat_area import ChatArea
from ui.components.chat_bubble import ChatBubble
from ui.components.speaker import MuteState

_PROJ = Path(__file__).resolve().parents[1]


def _wav_bytes(seconds: float = 0.05, rate: int = 16000, channels: int = 1, sampwidth: int = 2) -> bytes:
    """生成一段真实合法的 wav（正弦波），可指定采样率/声道/位深。"""
    buf = io.BytesIO()
    wf = wave.open(buf, "wb")
    wf.setnchannels(channels)
    wf.setsampwidth(sampwidth)
    wf.setframerate(rate)
    n = int(rate * seconds)
    for i in range(n):
        v = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
        if sampwidth == 2:
            frame = struct.pack("<h", v)
        else:
            frame = struct.pack("<i", v)
        wf.writeframesraw(frame * channels)
    wf.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# audio_utils
# ---------------------------------------------------------------------------


def test_pcm_to_int_rejects_8bit():
    with pytest.raises(ValueError):
        audio_utils.pcm_to_int(b"\x00\x01", 1)
    assert len(audio_utils.pcm_to_int(b"\x00\x00\x01\x00", 2)) == 2


def test_concat_rejects_mixed_params():
    a, b = _wav_bytes(rate=16000), _wav_bytes(rate=22050)
    params0, _ = audio_utils.wav_params_and_pcm(a)
    with pytest.raises(ValueError):
        audio_utils.concat_chunks_wav(params0, [a, b], ["一。", "二。"])


def test_concat_same_params_ok():
    a, b = _wav_bytes(rate=16000), _wav_bytes(rate=16000)
    params0, _ = audio_utils.wav_params_and_pcm(a)
    out = audio_utils.concat_chunks_wav(params0, [a, b], ["一。", "二。"])
    assert audio_utils.is_valid_wav(out)


def test_chunk_text_max_chars_and_clean():
    text = "零宽\u200b字符" + "很长的句子，" * 20
    chunks = audio_utils.chunk_text(text)
    assert chunks and all(len(c) <= audio_utils.MAX_CHARS for c in chunks)
    assert all("\u200b" not in c for c in chunks)


# ---------------------------------------------------------------------------
# TTSService（monkeypatch requests.post，不打真实网络）
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


@pytest.fixture
def fake_svc(tmp_path, monkeypatch):
    """指向死端口的 profile + 可编程的假 requests.post。"""
    prof = tmp_path / "assistant.yaml"
    prof.write_text(
        "name: assistant\n"
        "reference:\n"
        "  audio_path: ref.wav\n"
        '  text: "用户"\n'
        "api:\n"
        "  host: 127.0.0.1\n"
        "  port: 59999\n"
        "  timeout: 3\n",
        encoding="utf-8",
    )
    (tmp_path / "ref.wav").write_bytes(b"")
    calls = []

    def _post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _FakeResp(next(fake_svc.responses))

    fake_svc.responses = iter([])
    fake_svc.calls = calls
    monkeypatch.setattr("core.tts.service.requests.post", _post)
    fake_svc.svc = TTSService(profiles_dir=tmp_path)
    return fake_svc


def test_short_text_sends_cleaned_chunk(fake_svc):
    fake_svc.responses = iter([_wav_bytes(rate=16000)])
    r = fake_svc.svc.synthesize("你\u200b好")
    assert r.success
    sent = fake_svc.calls[0]["json"]["text"]
    assert sent == "你好"  # 清洗后的块文本，而非原始 text
    assert r.audio.sample_rate == 16000  # 真实解析值，不再硬编码 32000


def test_pure_punctuation_sends_no_request(fake_svc):
    r = fake_svc.svc.synthesize("。。。？！")
    assert r.success
    assert r.audio.data == b""
    assert fake_svc.calls == []  # 纯标点直接短路，不发请求


def test_long_text_param_mismatch_degrades_to_fail(fake_svc):
    long_text = "这是一句用来触发分块的长文本。" * 10  # >50 字 → 多块
    fake_svc.responses = iter(
        [_wav_bytes(rate=16000)] + [_wav_bytes(rate=22050)] * 20  # 第 2 块起参数不一致
    )
    r = fake_svc.svc.synthesize(long_text)
    assert r.success is False
    assert r.error_type == TTSErrorType.INVALID_AUDIO
    assert "参数不一致" in (r.error or "")


def test_long_text_ok_uses_real_sample_rate(fake_svc):
    long_text = "这是一句用来触发分块的长文本。" * 10
    fake_svc.responses = iter([_wav_bytes(rate=16000) for _ in range(24)])
    r = fake_svc.svc.synthesize(long_text)
    assert r.success, r.error
    assert r.audio.sample_rate == 16000
    assert audio_utils.is_valid_wav(r.audio.data)


# ---------------------------------------------------------------------------
# AudioPlayer：FIFO 串行播放
# ---------------------------------------------------------------------------


class _FakeWinsound:
    SND_FILENAME = 1

    def __init__(self):
        self.order = []

    def PlaySound(self, path, flags):
        self.order.append(path)
        time.sleep(0.01)


def test_player_fifo_serial_and_cleanup(tmp_path, monkeypatch):
    fake = _FakeWinsound()
    monkeypatch.setattr(player_module, "winsound", fake)
    monkeypatch.setattr(player_module, "_HAS_WINSOUND", True)
    monkeypatch.setattr(player_module, "_SND_FILENAME", fake.SND_FILENAME)

    p = player_module.AudioPlayer(cache_dir=str(tmp_path))
    assert p.play_bytes(_wav_bytes(0.03, rate=8000))
    assert p.play_bytes(_wav_bytes(0.04, rate=8000))
    p._queue.join()  # 等两条都播完

    assert len(fake.order) == 2  # 两条都播了
    assert all(not Path(f).exists() for f in fake.order)  # 临时文件都清理了


def test_player_single_worker_thread(tmp_path, monkeypatch):
    fake = _FakeWinsound()
    monkeypatch.setattr(player_module, "winsound", fake)
    monkeypatch.setattr(player_module, "_HAS_WINSOUND", True)
    monkeypatch.setattr(player_module, "_SND_FILENAME", fake.SND_FILENAME)

    p = player_module.AudioPlayer(cache_dir=str(tmp_path))
    for _ in range(5):
        p.play_bytes(_wav_bytes(0.02, rate=8000))
    p._queue.join()
    assert len(fake.order) == 5
    # 全程只有一个播放线程（串行消费队列 → 不并发、不互相截断）
    assert p._worker is not None and p._worker.is_alive()
    alive = p._worker
    p.play_bytes(_wav_bytes(0.02, rate=8000))
    p._queue.join()
    assert p._worker is alive  # 工作线程存活期间不重复创建


# ---------------------------------------------------------------------------
# MuteState 退订 / ChatArea 清理
# ---------------------------------------------------------------------------


def test_mute_state_unsubscribe():
    m = MuteState()
    cb = lambda _b: None  # noqa: E731
    m.subscribe(cb)
    assert cb in m._subs
    m.unsubscribe(cb)
    assert cb not in m._subs
    m.toggle()  # 退订后通知不再触达已移除的回调（不抛即通过）


class _FakePersona:
    name = "助手"
    title = "本地"
    address = None


def test_chat_area_clear_unsubscribes_bubbles():
    mute = MuteState()
    area = ChatArea(_FakePersona(), on_speak=lambda _t: None, mute_state=mute)
    area.add_assistant("第一句")
    area.add_assistant("第二句")
    assert len(mute._subs) == 2
    area.clear()
    assert len(mute._subs) == 0  # 气泡销毁 → 订阅全部退订，无泄漏


def test_bubble_exposes_mute_handle():
    mute = MuteState()
    bubble = ChatBubble.assistant("文本", on_speak=lambda _t: None, mute_state=mute)
    assert getattr(bubble, "_mute_state", None) is mute
    assert callable(getattr(bubble, "_mute_cb", None))


# ---------------------------------------------------------------------------
# AssistantApp 默认值 / 静音窗口期
# ---------------------------------------------------------------------------


def test_app_defaults_before_run():
    app = AssistantApp()
    assert app.mute_state is not None
    assert app.player is not None
    assert app.tts is None  # run() 前不崩、不发请求
    app._speak("用户")  # tts=None：静默跳过


class _MutingTTS:
    """合成期间把全局静音打开，模拟用户在请求飞行途中按了静音。"""

    def __init__(self, app):
        self.app = app
        self.calls = 0

    def synthesize(self, text, voice="assistant", text_lang="zh"):
        self.calls += 1
        self.app.mute_state.set(True)
        return TTSResult.ok(AudioData(data=_wav_bytes(), text=text))


class _CountingPlayer:
    def __init__(self):
        self.calls = 0

    def play(self, audio):
        self.calls += 1
        return True


def test_speak_muted_during_synthesis_not_played():
    app = AssistantApp()
    player = _CountingPlayer()
    app.player = player
    app.mute_state = MuteState()
    app.tts = _MutingTTS(app)
    app._speak("用户，欢迎回来。")
    assert app.tts.calls == 1  # 请求发出去了
    assert player.calls == 0  # 但合成期间被静音 → 结果作废不播


# ---------------------------------------------------------------------------
# tts_web 与共享模块同源
# ---------------------------------------------------------------------------


def _load_tts_web_module():
    import importlib.util

    path = _PROJ / "tts_web" / "tts_web.py"
    spec = importlib.util.spec_from_file_location("tts_web_check", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tts_web_shares_audio_utils():
    mod = _load_tts_web_module()
    # tts_web 按文件路径 importlib 加载共享模块（避免 requests 连带导入），
    # 与包导入的 audio_utils 是两个模块对象、函数身份必不相同；
    # 这里校验的是「同一份源码实现」——绑定的函数定义自同一个 .py 文件。
    utils_path = str((_PROJ / "core" / "tts" / "audio_utils.py").resolve()).replace("\\", "/")
    for fn in (mod.chunk_text, mod.concat_chunks_wav, mod.wav_params_and_pcm):
        assert Path(fn.__code__.co_filename).resolve().as_posix() == utils_path
    assert mod.MAX_CHARS == audio_utils.MAX_CHARS
    # 行为一致抽查：同一输入产出相同分块
    sample = "第一句。第二句，稍微长一点的话。第三句！"
    assert [c for c in mod.chunk_text(sample)] == audio_utils.chunk_text(sample)
