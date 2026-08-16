"""助手 Agent 的 TTS 层。

只暴露「文本 -> 音频数据」的能力，不依赖 Agent 核心、不依赖 Flet、不负责播放。
"""
from .models import AudioData, TTSErrorType, TTSResult
from .service import TTSService

__all__ = ["TTSService", "TTSResult", "AudioData", "TTSErrorType"]
