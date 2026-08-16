"""M2 测试：TTSService 的「文本 -> 音频数据」闭环与失败降级。

运行：在 <ASSISTANT_AGENT_DIR> 下用 .venv 的 pytest
  .venv/Scripts/python.exe -m pytest tests/test_tts_service.py -v

前置：GPT-SoVITS API 需在 127.0.0.1:9880 运行（Tests 1-4 需要；Test 5 / health_down 自动用死端口）。
"""
import socket

import pytest
from pathlib import Path

from core.tts.service import TTSService
from core.tts.models import TTSResult, TTSErrorType

# 一个几乎不可能被占用、用于「API 不可用」测试的死端口
DEAD_PORT = 59999

# 真实 API 测试需要本地 GPT-SoVITS 服务在 127.0.0.1:9880 运行。
# 若该服务未启动（例如只跑单测、没开 start_assistant.bat 的 TTS 窗口），
# 这些集成测试应「跳过」而非「失败」，避免把「服务没开」误判为架构/代码回归。
def _tts_server_up() -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.5)
    try:
        s.connect(("127.0.0.1", 9880))
        return True
    except OSError:
        return False
    finally:
        s.close()


SERVER_UP = _tts_server_up()
requires_server = pytest.mark.skipif(
    not SERVER_UP,
    reason="需要 GPT-SoVITS API 在 127.0.0.1:9880 运行（运行 start_assistant.bat 启动它）",
)

_REF = (
    Path(__file__).resolve().parents[1]
    / "core" / "tts" / "voice_profiles" / "assistant" / "reference.wav"
)


@pytest.fixture
def service() -> TTSService:
    return TTSService()  # 加载默认 profiles 目录（含 assistant）


def _dead_profile_yaml(ref_text: str) -> str:
    return (
        f'name: assistant\n'
        f'reference:\n'
        f'  audio_path: "{_REF}"\n'
        f'  text: "{ref_text}"\n'
        f'prompt_lang: zh\n'
        f'default_text_lang: zh\n'
        f'api:\n'
        f'  host: 127.0.0.1\n'
        f'  port: {DEAD_PORT}\n'
        f'  timeout: 3\n'
    )


# ---------- Tests 1-4：真实 API（需 9880 在线） ----------

@requires_server
def test_t1_chinese(service):
    r = service.synthesize("用户，欢迎回来。", text_lang="zh")
    assert isinstance(r, TTSResult)
    assert r.success, r.error
    assert r.audio and len(r.audio.data) > 0
    assert r.audio.format == "wav"


@requires_server
def test_t2_english(service):
    r = service.synthesize("Doctor, welcome back.", text_lang="en")
    assert r.success, r.error
    assert r.audio and len(r.audio.data) > 0


@requires_server
def test_t3_mixed(service):
    r = service.synthesize("用户，Good morning，今天一起努力吧。", text_lang="zh")
    assert r.success, r.error
    assert r.audio and len(r.audio.data) > 0


@requires_server
def test_t4_ultra_short(service):
    # 超短输入不应让服务崩溃，且应返回可用音频
    r = service.synthesize("嗯。", text_lang="zh")
    assert isinstance(r, TTSResult)        # 关键：不抛异常
    assert r.success, r.error
    assert r.audio and len(r.audio.data) > 0


# ---------- Test 5：API 不可用（死端口） ----------

def test_t5_api_unavailable(tmp_path):
    prof = tmp_path / "assistant.yaml"
    prof.write_text(_dead_profile_yaml("用户，你在忙吗？"), encoding="utf-8")
    svc = TTSService(profiles_dir=tmp_path)

    # 关键：调用不抛异常，返回失败结果 —— Agent 进程继续活着
    r = svc.synthesize("用户，欢迎回来。")
    assert isinstance(r, TTSResult)
    assert r.success is False
    assert r.error
    assert r.error_type in (
        TTSErrorType.CONNECTION,
        TTSErrorType.TIMEOUT,
        TTSErrorType.UNKNOWN,
    )


# ---------- health_check ----------

@requires_server
def test_health_check_up(service):
    assert service.health_check() is True


def test_health_check_down(tmp_path):
    prof = tmp_path / "assistant.yaml"
    prof.write_text(_dead_profile_yaml("x"), encoding="utf-8")
    svc = TTSService(profiles_dir=tmp_path)
    assert svc.health_check() is False
