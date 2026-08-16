"""TTS 文本分块与 WAV 拼接的共享工具（零第三方依赖，仅标准库）。

被两处复用，是唯一实现，防止两边漂移：
- core/tts/service.py  —— Agent 的 TTSService（相对导入）
- tts_web/tts_web.py   —— 诊断台（用 importlib 按文件路径加载本模块，
  以保持其「任意 Python（含 GPT-SoVITS runtime，无 requests）可跑」的约束，
  绕开 core/tts/__init__.py 对 requests/yaml 的连带导入）。

内容分两块：
1) 文本侧：不可见字符清洗 + 长文本分块（≤50 字/块，避开模型单次推理
   early_stop_num 截断漏字）；
2) 音频侧：WAV 解析（流式响应的 data size 常为占位值，PCM 直接取 data
   子块）+ 逐块 PCM 拼接（裁静音 → 首尾淡变 → 按边界类型插入可控停顿）。
"""
from __future__ import annotations

import array
import io
import re
import struct
import unicodedata
import wave

# ===================== 长文本对策 =====================
# GPT-SoVITS 单次推理有 early_stop_num = hz × max_sec 上限，500 字以上会截断漏字。
# 因此客户端先按长度分块（≤50 字），每块单独以 cut0 调一次 /tts，再拼接 WAV。
MAX_CHARS = 50

# 文本清洗：去掉不可见字符（网页/文档复制常夹带零宽空格等），避免其单独成块时
# TTS 返回 0 字节空音频，导致整段长文本合成中断。
_INVISIBLE = set("\u200b\u200c\u200d\ufeff")
_SPEECH_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaffA-Za-z0-9]")


def clean_invisible(text: str) -> str:
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


def has_speech(text: str) -> bool:
    """文本是否含可朗读字符（CJK/字母/数字）。纯标点/空串返回 False。"""
    return bool(_SPEECH_RE.search(text))


def chunk_text(text: str, max_chars: int = MAX_CHARS) -> list:
    """长文本分块：句末标点断句 → 超长句按逗号/顿号/冒号【贪心】切 → 仍超长按长度硬切。

    贪心合并：尽量把相邻的短分句并成一块，只在「不并就会超过 max_chars」时才断，
    避免每遇一个逗号就切一刀（那种切法会在句中插入大量不必要的接缝，听起来一顿一顿）。
    每块 ≤ max_chars 字，从根本上避开模型单次推理的 early_stop_num 截断。
    """
    text = clean_invisible(text.strip())
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
        c = clean_invisible(c)
        if not has_speech(c):
            if final:
                final[-1] = final[-1] + c
            continue
        final.append(c)
    return final


# ===================== WAV 解析 / 拼接 =====================

def is_valid_wav(data: bytes) -> bool:
    """最小 WAV 魔数校验：'RIFF' + 字节数 + 'WAVE'。"""
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def wav_params_and_pcm(raw: bytes):
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
SENT_END = set("。！？!?；;…")
PAUSE_SENT = 0.18
PAUSE_CLAUSE = 0.09
FADE = 0.02
TRIM_TAIL = 0.025
SIL_THR = 0.012

# WAV 8-bit PCM 是无符号的，'b'（有符号）会整体错位 128 产生直流偏置噪音；
# GPT-SoVITS 实际只输出 16-bit，这里只支持有符号定宽的 16/32 位，其余显式拒绝。
_PCM_ARRAY_CODE = {2: "h", 4: "i"}


def pcm_to_int(pcm: bytes, sampwidth: int):
    """PCM 字节转有符号采样数组。仅支持 16/32 位（8 位无符号不支持，显式报错）。"""
    try:
        code = _PCM_ARRAY_CODE[sampwidth]
    except KeyError:
        raise ValueError(f"不支持的采样位宽: {sampwidth}（仅支持 16/32 位 PCM）") from None
    return array.array(code, pcm)


def int_to_pcm(samples, sampwidth: int) -> bytes:
    return samples.tobytes()


def trim_silence(samples, sr: int):
    n = len(samples)
    if n == 0:
        return samples
    peak = max((abs(x) for x in samples), default=0) or 1
    th = SIL_THR * peak
    i = 0
    while i < n and abs(samples[i]) <= th:
        i += 1
    j = n - 1
    while j >= 0 and abs(samples[j]) <= th:
        j -= 1
    if j < i:
        return samples[0:0]
    tail = int(TRIM_TAIL * sr)
    return samples[i: min(n, j + 1 + tail)]


def apply_fades(samples, sr: int):
    n = len(samples)
    k = int(FADE * sr)
    for t in range(min(k, n)):
        if samples[t]:
            samples[t] = int(round(samples[t] * (t / k)))
        idx = n - 1 - t
        if samples[idx]:
            samples[idx] = int(round(samples[idx] * (t / k)))
    return samples


def pause_samples(sr: int, sampwidth: int, sec: float):
    return array.array(_PCM_ARRAY_CODE[sampwidth], [0]) * int(sec * sr)


def build_wav(params, pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(params.nchannels)
        wf.setsampwidth(params.sampwidth)
        wf.setframerate(params.framerate)
        wf.writeframes(pcm)
    return buf.getvalue()


def concat_chunks_wav(params, raw_chunks: list, texts: list) -> bytes:
    """逐块 PCM 经静音归一 + 首尾淡变 + 按边界类型插入可控停顿后拼接。

    只处理接缝处的静音/淡变/停顿，不增删任何文字对应的音频，故不会漏字。
    每块的实际参数（采样率/声道/位深）都会与第一块校验，不一致直接报错——
    按错误参数拼接会产出变速/失真音频，宁可让调用方拿到失败结果去降级。
    """
    sr = params.framerate
    sw = params.sampwidth
    combined = None
    for idx, (raw, txt) in enumerate(zip(raw_chunks, texts)):
        p, pcm = wav_params_and_pcm(raw)
        if (p.framerate, p.nchannels, p.sampwidth) != (sr, params.nchannels, sw):
            raise ValueError(
                f"第 {idx + 1} 块音频参数不一致（{p.framerate}Hz/{p.nchannels}ch/{p.sampwidth * 8}bit，"
                f"首块为 {sr}Hz/{params.nchannels}ch/{sw * 8}bit），拒绝拼接"
            )
        s = pcm_to_int(pcm, sw)
        s = trim_silence(s, sr)
        s = apply_fades(s, sr)
        if combined is None:
            combined = s
        else:
            prev = texts[idx - 1]
            sec = PAUSE_SENT if (prev and prev[-1] in SENT_END) else PAUSE_CLAUSE
            combined.extend(pause_samples(sr, sw, sec))
            combined.extend(s)
    return build_wav(params, int_to_pcm(combined, sw))
