"""本地音频播放器：把 AudioData / 原始 wav 字节落盘为临时 wav，再用系统后端播放。

设计约束（M3-A / 修复版）：
- 与 GPT-SoVITS / Agent 完全解耦，只认「wav 字节」，不关心 TTS 怎么来的。
- 失败降级不致命：播放失败只返回 False，绝不抛异常、绝不拖垮调用方。
- Windows 走 winsound；为了绕过 SND_ASYNC 在某些线程环境下静默失效的问题，
  实际播放以「同步阻塞」方式完成；调用方立即返回、UI 不阻塞，temp wav 播完删除。
- winsound 是进程级单声道：并发 PlaySound 会互相打断（连点多个气泡 🔊 时
  前一条被截断）。因此全部播放请求先进 FIFO 队列，由唯一的 daemon 工作线程
  串行播出——顺序完整、不重叠、不截断。
- 其它平台 winsound 不可用时静默降级（play 返回 False），不崩溃。

仅依赖标准库 + core.tts.models。
"""
from __future__ import annotations

import logging
import os
import queue
import tempfile
import threading
from typing import Optional

from core.tts.models import AudioData

logger = logging.getLogger("assistant.audio")

try:  # Windows 专用；非 Windows 宿主持有标志位静默降级
    import winsound  # type: ignore

    _HAS_WINSOUND = True
except Exception:  # noqa: BLE001 - 任何导入失败都视为不可用
    winsound = None  # type: ignore
    _HAS_WINSOUND = False

_SND_FILENAME = getattr(winsound, "SND_FILENAME", 0) if _HAS_WINSOUND else 0


def _is_valid_wav(data: bytes) -> bool:
    """与 TTSService 一致的轻量 wav 校验：RIFF/WAVE 头。"""
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


class AudioPlayer:
    """把 wav 字节落盘为临时文件，交给唯一播放线程按 FIFO 串行播放。

    调用方（Flet UI）调用 play() 后立即返回；排队、真正播放与文件清理在
    独立 daemon 线程中完成。进程内单例即可；内部线程安全。
    """

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self._cache_dir = cache_dir or os.path.join(tempfile.gettempdir(), "assistant_audio")
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
        except Exception:  # noqa: BLE001 - 建目录失败不致命，play 时再报错
            pass
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()

    @property
    def backend_available(self) -> bool:
        """后端是否可用（Windows+winsound）。不可用则 play 会优雅返回 False。"""
        return _HAS_WINSOUND

    def _write_temp(self, data: bytes) -> Optional[str]:
        """把字节写入唯一临时 wav，返回路径；失败返回 None。"""
        try:
            fd, path = tempfile.mkstemp(suffix=".wav", dir=self._cache_dir)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return path
        except Exception as exc:  # noqa: BLE001 - 落盘失败不致命
            logger.warning("写入临时 wav 失败：%s", exc)
            return None

    @staticmethod
    def _play_and_cleanup(path: str) -> None:
        """同步播放，然后删除临时文件。

        同步播放（flags 只传 SND_FILENAME）比 SND_ASYNC 更可靠：
        它不依赖调用线程的消息队列，且在函数返回前文件必须保持有效。
        """
        try:
            if _HAS_WINSOUND:
                winsound.PlaySound(path, _SND_FILENAME)
        except Exception as exc:  # noqa: BLE001 - 播放失败已在主调用方降级
            logger.debug("winsound 播放失败：%s", exc)
        finally:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as exc:  # noqa: BLE001
                logger.debug("删除临时 wav 失败：%s", exc)

    def _ensure_worker(self) -> None:
        """保证唯一的播放工作线程在运行（死了自动拉起）。"""
        with self._worker_lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(
                    target=self._worker_loop, daemon=True, name="assistant-audio-player"
                )
                self._worker.start()

    def _worker_loop(self) -> None:
        """串行消费播放队列：逐条同步播放，天然不会重叠/截断。"""
        while True:
            path = self._queue.get()
            try:
                if path is None:
                    return  # 哨兵：进程退出时由 daemon 属性兜底，正常不触发
                self._play_and_cleanup(path)
            except Exception as exc:  # noqa: BLE001 - 单条播放异常不终止工作线程
                logger.debug("播放线程异常：%s", exc)
            finally:
                self._queue.task_done()

    def play(self, audio: AudioData) -> bool:
        """播放一个 AudioData。成功返回 True，任何失败返回 False（不抛异常）。"""
        if audio is None or not getattr(audio, "data", b""):
            return False
        return self.play_bytes(audio.data, fmt=getattr(audio, "format", "wav"))

    def play_bytes(self, data: bytes, fmt: str = "wav") -> bool:
        """排队播放一段原始 wav 字节（FIFO 串行，后点的排队等前一条播完）。

        当前仅支持 wav（fmt='wav'）。入队成功即返回 True——真正播放是异步的。
        """
        if not data:
            return False
        if fmt != "wav" or not _is_valid_wav(data):
            logger.debug("AudioPlayer 只接受合法 wav 字节")
            return False
        if not _HAS_WINSOUND:
            logger.debug("非 Windows 或无 winsound，无法播放")
            return False

        path = self._write_temp(data)
        if path is None:
            return False

        self._queue.put(path)
        self._ensure_worker()
        logger.info("已排队播放助手语音（%d 字节）", len(data))
        return True
