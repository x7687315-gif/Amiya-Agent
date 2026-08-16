"""P3 · TTS 收尾：声音切换入口 + 语音可关闭开关。

边界（docs/12 §14）：UI → Agent → TTSService → GPT-SoVITS 单向解耦。
这里只验证 UI 层（Header 声音菜单 / 语音总开关）与配置解析，不触碰 TTSService 内部。
"""
import flet as ft
import pytest

from config import load_settings
from ui.app import AssistantApp
from ui.components.header import Header
from ui.components.speaker import MuteState


class _FakePersona:
    name = "助手"
    title = "本地"


# ---------- 配置：TTS_ENABLED 总开关 ----------


def test_tts_enabled_default_true(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("TTS_ENABLED", raising=False)
    assert load_settings().tts_enabled is True


def test_tts_enabled_off(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("TTS_ENABLED", "0")
    assert load_settings().tts_enabled is False


# ---------- Header：声音切换入口 ----------


def test_header_voice_menu_lists_and_checks_current():
    h = Header(_FakePersona(), voices=["assistant", "kelsey"], current_voice="assistant")
    assert isinstance(h._voice_menu, ft.PopupMenuButton)
    checked = {it.data: it.checked for it in h._voice_menu.items}
    assert checked == {"assistant": True, "kelsey": False}


def test_header_voice_switch_updates_and_calls_back():
    got = []
    h = Header(
        _FakePersona(),
        voices=["assistant", "kelsey"],
        current_voice="assistant",
        on_voice_change=got.append,
    )
    h._on_voice_select("kelsey")
    assert got == ["kelsey"]
    assert h._current_voice == "kelsey"
    checked = {it.data: it.checked for it in h._voice_menu.items}
    assert checked["kelsey"] is True and checked["assistant"] is False


def test_header_without_voices_hides_menu():
    h = Header(_FakePersona())  # 未传 voices
    assert not isinstance(h._voice_menu, ft.PopupMenuButton)


def test_header_voice_controls_hidden_when_tts_disabled():
    h = Header(
        _FakePersona(),
        mute_state=MuteState(),
        voices=["assistant"],
        current_voice="assistant",
        show_voice_controls=False,
    )
    assert h._mute_btn.visible is False
    assert not isinstance(h._voice_menu, ft.PopupMenuButton)


# ---------- app：语音功能总开关（可关闭） ----------


class _FakeTTS:
    def __init__(self):
        self.calls = 0

    def synthesize(self, text, voice, text_lang):
        self.calls += 1
        raise AssertionError("TTS 关闭时不应发起合成")


def test_speak_skipped_when_tts_disabled():
    app = AssistantApp()
    app.tts = _FakeTTS()
    app._tts_enabled = False  # 语音总开关关闭
    # 不应抛错、不应调用 synthesize（_FakeTTS 被调到会 AssertionError）
    app._speak("用户，欢迎回来。")
    assert app.tts.calls == 0
