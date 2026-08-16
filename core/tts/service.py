"""TTSService：GPT-SoVITS /tts 的薄封装 HTTP 客户端。

职责唯一：给定文本 + voice，调用 localhost:9880 的 /tts，返回音频数据。
不知道 Agent 是什么，不知道 Flet 是什么，不负责播放。

失败策略：synthesize() / health_check() 内部捕获所有异常，转换为
TTSResult(success=False, ...)。语音失败绝不让调用方（Agent）崩溃。

依赖：仅 requests + PyYAML（与 Agent 现有环境一致，无新增依赖）。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

import re
import struct
import io
import wave
import array
import unicodedata

import time

import requests
import yaml

from .models import AudioData, TTSResult, TTSErrorType


# ===================== 长文本对策（与 tts_web 一致）=====================
# GPT-SoVITS 单次推理有 early_stop_num = hz × max_sec 上限，500 字以上会截断漏字。
# 因此客户端先按长度分块（≤50 字），每块单独以 cut0 调一次 /tts，再拼接 WAV。
_MAX_CHARS = 50

# 文本清洗：去掉不可见字符（网页/文档复制常夹带零宽空格等），避免其单独成块时
# TTS 返回 0 字节空音频，导致整段长文本合成中断。
_INVISIBLE = set("\u200b\u200c\u200d\ufeff")
_SPEECH_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaffA-Za-z0-9]")


def _clean_invisible(text: str) -> str:
    """去掉零宽空格/连字符、字节序标记等不可见控制字符（保留正常换行/制表符）。"""
    out = []
    for ch in text:
        if ch in _INVISIBLE:
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf") and ch not in "\n\t\r":
            continue
        out.append(ch)
    return "".join(out)


def _has_speech(text: str) -> bool:
    """文本是否含可朗读字符（CJK/字母/数字）。纯标点/空串返回 False。"""
    return bool(_SPEECH_RE.search(text))


def _chunk_text(text: str, max_chars: int = _MAX_CHARS) -> list:
    """长文本分块：句末标点断句 → 超长句按逗号/顿号/冒号【贪心】切 → 仍超长按长度硬切。

    贪心合并：尽量把相邻的短分句并成一块，只在「不并就会超过 max_chars」时才断，
    避免每遇一个逗号就切一刀（那种切法会在句中插入大量不必要的接缝，听起来一顿一顿）。
    每块 ≤ max_chars 字，从根本上避开模型单次推理的 early_stop_num 截断。
    """
    text = _clean_invisible(text.strip())
    if not text:
        return []
    out = []
    for raw in re.split(r"(?<=[。！？!?；;…\n])", text):
        s = raw.strip()
        if not s:
            continue
        if len(s) <= max_chars:
            out.append(s)
            continue
        buf = ""
        for sub in re.split(r"(?<=[，,、：:])", s):
            sub = sub.strip()
            if not sub:
                continue
            if not buf:
                buf = sub
            elif len(buf) + len(sub) <= max_chars:
                buf = buf + sub
            else:
                out.append(buf)
                buf = sub
        if buf:
            out.append(buf)
    hard = []
    for c in out:
        if len(c) <= max_chars:
            hard.append(c)
        else:
            for i in range(0, len(c), max_chars):
                hard.append(c[i:i + max_chars])
    merged = []
    for c in hard:
        if (merged and len(c) < 4
                and merged[-1][-1] not in "。！？!?；;…"
                and len(merged[-1]) + len(c) <= max_chars + 4):
            merged[-1] = merged[-1] + c
        else:
            merged.append(c)

    # 清理：丢弃空块；纯标点/不可见块并入上一块（否则 TTS 返回空音频导致整段中断）
    final = []
    for c in merged:
        c = _clean_invisible(c)
        if not _has_speech(c):
            if final:
                final[-1] = final[-1] + c
            continue
        final.append(c)
    return final


def _wav_params_and_pcm(raw: bytes):
    """解析 WAV：用 wave 取参数，但 PCM 直接取 'data' 子块后。

    GPT-SoVITS streaming 返回的 WAV，其 header 里的 data size 常为占位值(0)，
    wave.readframes 会据此返回 0 帧，因此 PCM 改为直接从 'data' 子块后读取。
    """
    w = wave.open(io.BytesIO(raw), "rb")
    params = w.getparams()
    w.close()
    idx = raw.find(b"data")
    if idx < 0:
        raise ValueError("WAV 缺少 data 子块")
    data_size = struct.unpack("<I", raw[idx + 4:idx + 8])[0]
    pcm_start = idx + 8
    if 0 < data_size <= len(raw) - pcm_start:
        pcm = raw[pcm_start:pcm_start + data_size]
    else:
        pcm = raw[pcm_start:]
    return params, pcm


# ---------- 接缝平滑：淡入淡出 + 静音归一 + 可控停顿 ----------
# 逐块独立合成后若直接拼接 PCM，块间会产生硬切/爆音/突兀换气（每段都被模型当成
# 独立成句重新起音）。这里在拼接前对每块做：裁掉首尾多余静音 → 首尾各做短淡变 →
# 按边界类型插入可控停顿，使接缝变成自然的换气/标点停顿，且不增删任何文字音频。
_SENT_END = set("。！？!?；;…")
_PAUSE_SENT = 0.18
_PAUSE_CLAUSE = 0.09
_FADE = 0.02
_TRIM_TAIL = 0.025
_SIL_THR = 0.012


def _pcm_to_int(pcm: bytes, sampwidth: int):
    return array.array({1: "b", 2: "h", 4: "i"}[sampwidth], pcm)


def _int_to_pcm(samples, sampwidth: int) -> bytes:
    return samples.tobytes()


def _trim_silence(samples, sr: int):
    n = len(samples)
    if n == 0:
        return samples
    peak = max((abs(x) for x in samples), default=0) or 1
    th = _SIL_THR * peak
    i = 0
    while i < n and abs(samples[i]) <= th:
        i += 1
    j = n - 1
    while j >= 0 and abs(samples[j]) <= th:
        j -= 1
    if j < i:
        return samples[0:0]
    tail = int(_TRIM_TAIL * sr)
    return samples[i: min(n, j + 1 + tail)]


def _apply_fades(samples, sr: int):
    n = len(samples)
    k = int(_FADE * sr)
    for t in range(min(k, n)):
        if samples[t]:
            samples[t] = int(round(samples[t] * (t / k)))
        idx = n - 1 - t
        if samples[idx]:
            samples[idx] = int(round(samples[idx] * (t / k)))
    return samples


def _pause_samples(sr: int, sampwidth: int, sec: float):
    return array.array({1: "b", 2: "h", 4: "i"}[sampwidth], [0]) * int(sec * sr)


def _concat_chunks_wav(params, raw_chunks: list, texts: list) -> bytes:
    """逐块 PCM 经静音归一 + 首尾淡变 + 按边界类型插入可控停顿后拼接。

    只处理接缝处的静音/淡变/停顿，不增删任何文字对应的音频，故不会漏字。
    """
    sr = params.framerate
    sw = params.sampwidth
    combined = None
    for idx, (raw, txt) in enumerate(zip(raw_chunks, texts)):
        _, pcm = _wav_params_and_pcm(raw)
        s = _pcm_to_int(pcm, sw)
        s = _trim_silence(s, sr)
        s = _apply_fades(s, sr)
        if combined is None:
            combined = s
        else:
            prev = texts[idx - 1]
            sec = _PAUSE_SENT if (prev and prev[-1] in _SENT_END) else _PAUSE_CLAUSE
            combined.extend(_pause_samples(sr, sw, sec))
            combined.extend(s)
    return _build_wav(params, _int_to_pcm(combined, sw))


def _build_wav(params, pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(params.nchannels)
        wf.setsampwidth(params.sampwidth)
        wf.setframerate(params.framerate)
        wf.writeframes(pcm)
    return buf.getvalue()

_DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent / "voice_profiles"


def _is_valid_wav(data: bytes) -> bool:
    """最小 WAV 魔数校验：'RIFF' + 字节数 + 'WAVE'。"""
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


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

        # 长文本对策：客户端分块，逐块 /tts(cut0)，拼接 WAV，避免模型单次推理截断漏字。
        chunks = _chunk_text(text)
        if len(chunks) <= 1:
            # 短文本：保持原有单次调用逻辑
            payload = {
                "text": text,
                "text_lang": t_lang,
                "ref_audio_path": ref_audio,
                "prompt_text": ref_text,
                "prompt_lang": prompt_lang,
                "media_type": media_type,
                "streaming_mode": bool(api.get("streaming_mode", False)),
                "batch_size": 1,
                "text_split_method": api.get("text_split_method", "cut5"),
            }
            return self._single_synth(url, payload, media_type, text, req_timeout)

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
            # 兜底：上游 _chunk_text 已过滤纯标点/空块；若仍出现不可合成块，跳过不报错中断整段
            if not res.audio or not res.audio.data or len(res.audio.data) < 44:
                continue
            try:
                if params0 is None:
                    params0, _ = _wav_params_and_pcm(res.audio.data)
                raw_list.append(res.audio.data)
                used_texts.append(ch)
            except Exception as exc:
                return TTSResult.fail(f"长文本音频解析失败: {exc}", TTSErrorType.INVALID_AUDIO)
        if not raw_list:
            return TTSResult.fail("长文本合成后音频为空", TTSErrorType.EMPTY_RESPONSE)
        data = _concat_chunks_wav(params0, raw_list, used_texts)
        audio = AudioData(data=data, format="wav", sample_rate=32000, text=text)
        return TTSResult.ok(audio)

    def _single_synth(self, url, payload, media_type, text, timeout, max_retry: int = 3) -> TTSResult:
        """单次 /tts 调用 + 统一异常处理（供短文本直调与长文本分块复用）。

        对“空响应”做指数退避重试：GPT-SoVITS 偶发返回 200 但 body 为空，
        多见于模型刚 ready / GPU 忙 / 短文本，重试通常即可恢复。

        无效输入（纯标点/空/不可见字符）不调用 TTS、也不重试，直接返回空结果，
        交由调用方（长文本循环）跳过——重试这类必然失败的输入无意义。
        """
        # 无效输入：不调 TTS、不重试，返回空结果（调用方据此跳过该块）
        if not _has_speech(_clean_invisible(text)):
            return TTSResult.ok(
                AudioData(data=b"", format=media_type, sample_rate=32000, text=text)
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
                if media_type == "wav" and not _is_valid_wav(data):
                    return TTSResult.fail("TTS 返回内容不是合法 WAV", TTSErrorType.INVALID_AUDIO)
                audio = AudioData(data=data, format=media_type, sample_rate=32000, text=text)
                return TTSResult.ok(audio)
            # 空响应：瞬态，重试
            last_err = f"TTS 返回空响应（第 {attempt} 次）"
            if attempt < max_retry:
                time.sleep(2 ** attempt)   # 退避 2s / 4s / 8s
                continue
        return TTSResult.fail(f"重试 {max_retry} 次仍{last_err}", TTSErrorType.EMPTY_RESPONSE)

    # ---------- 健康检查（M4 一键启动要用） ----------
    def health_check(self, voice: str = "") -> bool:
        """轻量探活：API 是否在线。只探 localhost:9880 的可达性，

        不依赖具体 WebUI 路由。连接成功（哪怕返回 404）即视为在线；
        连接被拒/超时返回 False，且绝不抛异常。
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
