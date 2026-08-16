"""TTSService：GPT-SoVITS /tts 的薄封装 HTTP 客户端。

职责唯一：给定文本 + voice，调用 localhost:9880 的 /tts，返回音频数据。
不知道 Agent 是什么，不知道 Flet 是什么，不负责播放。

失败策略：synthesize() / health_check() 内部捕获所有异常，转换为
TTSResult(success=False, ...)。语音失败绝不让调用方（Agent）崩溃。

依赖：仅 requests + PyYAML（与 Agent 现有环境一致，无新增依赖）。
文本分块 / WAV 拼接的实现在 .audio_utils（与 tts_web 诊断台共享，单一实现）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import time

import requests
import yaml

from .audio_utils import (
    chunk_text,
    clean_invisible,
    concat_chunks_wav,
    has_speech,
    is_valid_wav,
    wav_params_and_pcm,
)
from .models import AudioData, TTSResult, TTSErrorType


_DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent / "voice_profiles"


class TTSService:
    def __init__(
        self,
        profiles_dir: Optional[os.PathLike | str] = None,
        default_voice: str = "assistant",
    ) -> None:
        self._profiles_dir = Path(profiles_dir or _DEFAULT_PROFILES_DIR)
        self._default_voice = default_voice
        self._profiles: Dict[str, dict] = self._load_profiles(self._profiles_dir)

    # ---------- profile 加载 ----------
    @staticmethod
    def _load_profiles(directory: Path) -> Dict[str, dict]:
        profiles: Dict[str, dict] = {}
        if not directory.exists():
            return profiles
        for yaml_path in sorted(directory.glob("*.yaml")):
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            except Exception:
                # 单个 profile 损坏不应拖累整个服务
                continue
            name = cfg.get("name") or yaml_path.stem
            cfg["_file"] = str(yaml_path)  # 内部用：解析相对路径的锚点
            profiles[name] = cfg
        return profiles

    def list_voices(self) -> list[str]:
        return sorted(self._profiles.keys())

    def get_profile(self, voice: str) -> dict:
        try:
            return self._profiles[voice]
        except KeyError:
            raise KeyError(f"未知 voice: {voice!r}（可用: {self.list_voices()}）")

    @staticmethod
    def _resolve_path(profile: dict, rel: str) -> str:
        base = Path(profile.get("_file", "")).resolve().parent
        return str((base / rel).resolve())

    # ---------- 核心接口 ----------
    def synthesize(
        self,
        text: str,
        voice: str = "",
        text_lang: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> TTSResult:
        """把一段文本合成为音频数据。

        参数：
            text:       待合成文本
            voice:      语音配置名（默认 assistant）
            text_lang:  合成文本语言（默认取 profile.default_text_lang）
            timeout:    单次请求超时秒数（默认取 profile.api.timeout）
        返回：TTSResult（success=True 带 AudioData；False 带 error）
        """
        voice = voice or self._default_voice

        try:
            profile = self.get_profile(voice)
        except KeyError as exc:
            return TTSResult.fail(str(exc), TTSErrorType.UNKNOWN)

        api = profile.get("api", {})
        host = api.get("host", "127.0.0.1")
        port = int(api.get("port", 9880))
        url = f"http://{host}:{port}/tts"
        req_timeout = timeout if timeout is not None else int(api.get("timeout", 30))

        ref = profile.get("reference", {})
        ref_audio = ref.get("audio_path", "")
        ref_text = ref.get("text", "")
        if not ref_audio:
            return TTSResult.fail("voice profile 缺少 reference.audio_path", TTSErrorType.UNKNOWN)
        if not os.path.isabs(ref_audio):
            ref_audio = self._resolve_path(profile, ref_audio)

        prompt_lang = profile.get("prompt_lang", "zh")
        t_lang = text_lang or profile.get("default_text_lang", "zh")

        media_type = api.get("media_type", "wav")

        # 长文本对策：客户端分块（顺带完成不可见字符清洗），逐块 /tts(cut0)，
        # 拼接 WAV，避免模型单次推理截断漏字。所有路径统一发「清洗后的块文本」。
        chunks = chunk_text(text)
        if not chunks:
            # 纯标点 / 空白 / 仅不可见字符：无内容可读，不发请求
            return TTSResult.ok(AudioData(data=b"", format=media_type, sample_rate=None, text=text))
        if len(chunks) == 1:
            # 短文本：保持原有单次调用逻辑
            payload = {
                "text": chunks[0],
                "text_lang": t_lang,
                "ref_audio_path": ref_audio,
                "prompt_text": ref_text,
                "prompt_lang": prompt_lang,
                "media_type": media_type,
                "streaming_mode": bool(api.get("streaming_mode", False)),
                "batch_size": 1,
                "text_split_method": api.get("text_split_method", "cut5"),
            }
            return self._single_synth(url, payload, media_type, chunks[0], req_timeout)

        # 长文本：逐块合成，平滑拼接（淡入淡出 + 可控停顿），避免块间硬拼接的突兀/爆音
        raw_list = []
        used_texts = []
        params0 = None
        for ch in chunks:
            payload = {
                "text": ch,
                "text_lang": t_lang,
                "ref_audio_path": ref_audio,
                "prompt_text": ref_text,
                "prompt_lang": prompt_lang,
                "media_type": "wav",
                "streaming_mode": True,
                "batch_size": 1,
                "text_split_method": "cut0",   # 单块即单段
            }
            res = self._single_synth(url, payload, "wav", ch, req_timeout)
            if not res.success:
                return TTSResult.fail(f"长文本分段合成失败: {res.error}", TTSErrorType.UNKNOWN)
            # 兜底：上游 chunk_text 已过滤纯标点/空块；若仍出现不可合成块，跳过不报错中断整段
            if not res.audio or not res.audio.data or len(res.audio.data) < 44:
                continue
            try:
                if params0 is None:
                    params0, _ = wav_params_and_pcm(res.audio.data)
                raw_list.append(res.audio.data)
                used_texts.append(ch)
            except Exception as exc:
                return TTSResult.fail(f"长文本音频解析失败: {exc}", TTSErrorType.INVALID_AUDIO)
        if not raw_list:
            return TTSResult.fail("长文本合成后音频为空", TTSErrorType.EMPTY_RESPONSE)
        try:
            data = concat_chunks_wav(params0, raw_list, used_texts)
        except Exception as exc:
            # 参数不一致 / 位宽不支持等：拼不出合法音频，交由调用方降级
            return TTSResult.fail(f"长文本音频拼接失败: {exc}", TTSErrorType.INVALID_AUDIO)
        audio = AudioData(data=data, format="wav", sample_rate=params0.framerate, text=text)
        return TTSResult.ok(audio)

    def _single_synth(self, url, payload, media_type, text, timeout, max_retry: int = 3) -> TTSResult:
        """单次 /tts 调用 + 统一异常处理（供短文本直调与长文本分块复用）。

        对“空响应”做指数退避重试：GPT-SoVITS 偶发返回 200 但 body 为空，
        多见于模型刚 ready / GPU 忙 / 短文本，重试通常即可恢复。

        无效输入（纯标点/空/不可见字符）不调用 TTS、也不重试，直接返回空结果，
        交由调用方（长文本循环）跳过——重试这类必然失败的输入无意义。
        """
        # 无效输入：不调 TTS、不重试，返回空结果（调用方据此跳过该块）
        if not has_speech(clean_invisible(text)):
            return TTSResult.ok(
                AudioData(data=b"", format=media_type, sample_rate=None, text=text)
            )
        last_err = "TTS 返回空响应"
        for attempt in range(1, max_retry + 1):
            try:
                resp = requests.post(url, json=payload, timeout=timeout)
            except requests.exceptions.ConnectionError:
                return TTSResult.fail("TTS 服务不可用（连接被拒绝）", TTSErrorType.CONNECTION)
            except requests.exceptions.Timeout:
                return TTSResult.fail("TTS 请求超时", TTSErrorType.TIMEOUT)
            except requests.exceptions.RequestException as exc:
                return TTSResult.fail(f"TTS 请求异常: {exc}", TTSErrorType.UNKNOWN)

            if resp.status_code != 200:
                return TTSResult.fail(f"TTS 返回 HTTP {resp.status_code}", TTSErrorType.HTTP_ERROR)

            data = resp.content
            if data:
                if media_type == "wav" and not is_valid_wav(data):
                    return TTSResult.fail("TTS 返回内容不是合法 WAV", TTSErrorType.INVALID_AUDIO)
                sample_rate = None
                if media_type == "wav":
                    try:
                        sample_rate = wav_params_and_pcm(data)[0].framerate
                    except Exception:
                        sample_rate = None
                audio = AudioData(data=data, format=media_type, sample_rate=sample_rate, text=text)
                return TTSResult.ok(audio)
            # 空响应：瞬态，重试
            last_err = f"TTS 返回空响应（第 {attempt} 次）"
            if attempt < max_retry:
                time.sleep(2 ** attempt)   # 退避 2s / 4s / 8s
                continue
        return TTSResult.fail(f"重试 {max_retry} 次仍{last_err}", TTSErrorType.EMPTY_RESPONSE)

    # ---------- 健康检查（M4 一键启动要用） ----------
    def health_check(self, voice: str = "") -> bool:
        """轻量探活：API 是否在线（只探可达性，不依赖具体 WebUI 路由）。

        连接成功（哪怕返回 404）即视为在线；连接被拒/超时返回 False，且绝不抛异常。
        """
        voice = voice or self._default_voice
        try:
            profile = self.get_profile(voice)
        except KeyError:
            return False
        api = profile.get("api", {})
        host = api.get("host", "127.0.0.1")
        port = int(api.get("port", 9880))
        url = f"http://{host}:{port}/"
        try:
            requests.get(url, timeout=2)
            return True
        except requests.exceptions.RequestException:
            return False
