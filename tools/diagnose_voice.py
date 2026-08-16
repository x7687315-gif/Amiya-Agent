"""语音链路诊断脚本：独立测试 TTS 合成 + 多种播放方式。

用法：
    .venv\\Scripts\\python.exe tools\\diagnose_voice.py

会打印每一步结果，帮助定位是 TTS 没返回音频，还是 winsound 播放失败。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.tts.service import TTSService
from core.tts.player import AudioPlayer


def _winsound_info():
    try:
        import winsound
        has_sync = hasattr(winsound, "SND_SYNC")
        print(
            f"[winsound] 可用，SND_ASYNC={winsound.SND_ASYNC}, "
            f"SND_FILENAME={winsound.SND_FILENAME}, SND_SYNC={has_sync}"
        )
        return True
    except Exception as exc:
        print(f"[winsound] 不可用：{exc}")
        return False


def _test_tts(text: str):
    print(f"\n[TTS] 测试文本：{text!r}")
    tts = TTSService()
    result = tts.synthesize(text=text, voice="assistant", text_lang="zh")
    print(f"[TTS] success={result.success}, error={result.error!r}")
    if result.success and result.audio:
        print(f"[TTS] audio.format={result.audio.format}, len(data)={len(result.audio.data)}")
        return result.audio
    return None


def _test_player_sync(audio):
    print("\n[播放] 测试 winsound 同步播放（flags=0，会阻塞直到放完）...")
    try:
        import winsound
        path = os.path.join(os.environ.get("TEMP", "."), "assistant_diag_sync.wav")
        with open(path, "wb") as f:
            f.write(audio.data)
        # SND_SYNC 常量在某些 Python 构建中不存在，但 flags=0 即同步播放
        flags = winsound.SND_FILENAME
        if hasattr(winsound, "SND_SYNC"):
            flags |= winsound.SND_SYNC
        winsound.PlaySound(path, flags)
        print("[播放] 同步播放返回，未抛异常")
    except Exception as exc:
        print(f"[播放] 同步播放异常：{exc}")
        traceback.print_exc()


def _test_player_async(audio):
    print("\n[播放] 测试 winsound.SND_ASYNC...")
    try:
        import winsound
        path = os.path.join(os.environ.get("TEMP", "."), "assistant_diag_async.wav")
        with open(path, "wb") as f:
            f.write(audio.data)
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        print("[播放] SND_ASYNC 返回，未抛异常；等待 3 秒让后台播放...")
        time.sleep(3)
    except Exception as exc:
        print(f"[播放] SND_ASYNC 异常：{exc}")
        traceback.print_exc()


def _test_player_audio_player(audio):
    print("\n[播放] 测试 AudioPlayer.play（当前实现）...")
    player = AudioPlayer()
    ok = player.play(audio)
    print(f"[播放] AudioPlayer.play 返回：{ok}")
    time.sleep(3)


def _test_play_in_thread(audio):
    print("\n[播放] 测试在后台线程里用 SND_ASYNC 播放...")
    def _play():
        try:
            import winsound
            path = os.path.join(os.environ.get("TEMP", "."), "assistant_diag_thread.wav")
            with open(path, "wb") as f:
                f.write(audio.data)
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            print("[后台线程] SND_ASYNC 调用返回")
        except Exception as exc:
            print(f"[后台线程] 异常：{exc}")
            traceback.print_exc()
    t = threading.Thread(target=_play)
    t.start()
    t.join()
    time.sleep(3)


def main():
    print("=" * 60)
    print("助手语音链路诊断")
    print("=" * 60)
    _winsound_info()

    # 用一句已验证的中文测试
    audio = _test_tts("用户，欢迎回来。")
    if audio is None:
        print("\n[TTS] 合成失败，跳过播放测试。")
        return 1

    # 保存一份到项目 logs 目录供人工试听
    logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(logs_dir, exist_ok=True)
    sample_path = os.path.join(logs_dir, "tts_sample.wav")
    with open(sample_path, "wb") as f:
        f.write(audio.data)
    print(f"[文件] 已保存样例音频：{sample_path}")

    _test_player_sync(audio)
    _test_player_async(audio)
    _test_play_in_thread(audio)
    _test_player_audio_player(audio)

    print("\n" + "=" * 60)
    print("诊断结束。如果 SND_SYNC 能听到声音而 SND_ASYNC / AudioPlayer 听不到，")
    print("说明 winsound.SND_ASYNC 在当前进程/线程环境下不可靠。")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
