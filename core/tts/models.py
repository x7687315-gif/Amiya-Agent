"""TTS 结果模型。

与 Agent 现有「失败降级不致命」的设计一致：TTSService 永远通过
TTSResult 把成功/失败返回给调用方，绝不向上抛异常。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TTSErrorType(str, Enum):
    """TTS 失败的分类。便于上层做差异化降级。"""

    CONNECTION = "connection_refused"   # API 进程不在 / 端口无监听
    TIMEOUT = "timeout"                 # 单次请求超时
    HTTP_ERROR = "http_error"           # 4xx / 5xx
    INVALID_AUDIO = "invalid_audio"     # 返回内容不是合法音频
    EMPTY_RESPONSE = "empty_response"   # HTTP 200 但 body 为空
    UNKNOWN = "unknown"                 # 其他未归类异常


@dataclass
class AudioData:
    """一段音频数据。格式由 format 描述（v1 固定 wav），不含任何播放逻辑。

    播放完全归 UI 层管；TTSService 只负责「文本 -> 音频数据」。
    """

    data: bytes
    format: str = "wav"                 # wav | raw | ogg | aac（官方 API 支持，v1 用 wav）
    sample_rate: Optional[int] = None   # 已知时填入，未知留 None
    text: str = ""                      # 回声本次合成文本，便于上层 UI 关联气泡

    @property
    def is_valid(self) -> bool:
        return self.data is not None and len(self.data) > 0


@dataclass
class TTSResult:
    """TTS 合成结果。

    success=False 时 audio=None，error 描述原因、error_type 给分类。
    调用方只消费这个结果，绝不该让 TTSService 的异常冒泡到 Agent 主流程。
    """

    success: bool
    audio: Optional[AudioData] = None
    error: Optional[str] = None
    error_type: Optional[TTSErrorType] = None

    @classmethod
    def ok(cls, audio: AudioData) -> "TTSResult":
        return cls(success=True, audio=audio)

    @classmethod
    def fail(
        cls,
        error: str,
        error_type: TTSErrorType = TTSErrorType.UNKNOWN,
    ) -> "TTSResult":
        return cls(success=False, audio=None, error=error, error_type=error_type)
