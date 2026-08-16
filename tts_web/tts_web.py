#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TTS 诊断 Web 工具 —— 绕过 Agent 调用层，直连 GPT-SoVITS /tts 验证模型本身。

目的：
    之前 Agent 报 "TTS 服务不可用（连接被拒绝）"，根因是 TTS 进程没活着。
    本工具用一套干净的页面直连 127.0.0.1:9880 的 /tts，把模型真实输出变成
    可下载的音频（WAV / MP4），并显示合成进度，用来判断：
        - 模型本身是否正常出声？
        - 还是我们之前的调用代码有问题？

长文本对策（关键）：
    GPT-SoVITS 单次推理有 early_stop_num = hz × max_sec 上限，社区实测 500 字以上
    就会触发截断、漏字（即使 text_split_method=cut5 也会，因为单句仍可能超上限）。
    因此本工具在【客户端】先按长度分块（每块 ≤ MAX_CHARS 字），每块单独以
    cut0（整段不切）调一次 /tts，再把各块 WAV 拼接成一个完整音频。这样每块都
    远小于模型单次上限，从根本上杜绝漏字。这是社区公认的分段合成 + 拼接方案。

    设计：
    - 后端仅用 Python 标准库；文本分块 / WAV 拼接与 core/tts/service.py 共享
      同一份实现（core/tts/audio_utils.py，亦为纯标准库），防止两边漂移。
    - 前端（index.html）与后端同源，免去 CORS；后端再以服务端身份转发到 9880。
    - /tts 用流式返回（与 Agent 侧 TTSService 一致，streaming_mode=True）；
      拼接后的音频落盘到 outputs/。
    - 核心合成逻辑抽成 synthesize_to_file()，Web 接口与离线脚本共用。

配置与 Agent 的 core/tts/voice_profiles/assistant.yaml 保持一致，集中放在下面的 CONFIG。

运行：
    python tts_web.py
    然后浏览器打开 http://127.0.0.1:<端口>/
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import time
import http.server
import http.client
from urllib.parse import urlparse, unquote

# 共享工具模块（文本分块 / WAV 解析与拼接）：按文件路径直接加载，
# 不走 core.tts 包导入——core/tts/__init__.py 会连带 import requests/yaml，
# 而本诊断台要求任意 Python（含无第三方包的 GPT-SoVITS runtime）可跑。
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJ_ROOT = os.path.dirname(_HERE)
_UTILS_PATH = os.path.join(_PROJ_ROOT, "core", "tts", "audio_utils.py")
_spec = importlib.util.spec_from_file_location("assistant_tts_audio_utils", _UTILS_PATH)
if _spec is None or _spec.loader is None:
    raise ImportError(f"无法加载共享工具模块: {_UTILS_PATH}")
audio_utils = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audio_utils)

chunk_text = audio_utils.chunk_text
clean_invisible = audio_utils.clean_invisible
has_speech = audio_utils.has_speech
wav_params_and_pcm = audio_utils.wav_params_and_pcm
concat_chunks_wav = audio_utils.concat_chunks_wav

# ============================ CONFIG（与 assistant.yaml 对齐）============================
TTS_HOST = "127.0.0.1"
TTS_PORT = 9880

# 参考音频必须是绝对路径（TTS 进程按此路径从磁盘读取，不能用相对路径）
REF_AUDIO_PATH = "<ASSISTANT_AGENT_DIR>/core/tts/voice_profiles/assistant/reference.wav"
PROMPT_TEXT = "用户，你在忙吗？"          # 参考音频的准确文字（M1 已人工核对）
PROMPT_LANG = "zh"
DEFAULT_TEXT_LANG = "zh"

# 长文本分块参数（MAX_CHARS 与共享模块保持同源，避免两处再漂移）
MAX_CHARS = audio_utils.MAX_CHARS
PER_CHUNK_SPLIT = "cut0"                  # 单块即单段，禁止 API 内部再切分（防 cut5 仍漏字）

# ffmpeg：仅 MP4 输出需要
FFMPEG = "<GPT_SOVITS_DIR>/runtime/ffmpeg.exe"

WEB_PORTS = [8000, 8080, 8888]            # 依次尝试，首个可用者
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
INDEX_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")

os.makedirs(OUT_DIR, exist_ok=True)
# ===================================================================================


def tts_up() -> bool:
    """轻量探活：9880 能否建立 TCP 连接。"""
    try:
        conn = http.client.HTTPConnection(TTS_HOST, TTS_PORT, timeout=2)
        conn.connect()
        conn.close()
        return True
    except Exception:
        return False


def _emit(wfile, obj) -> None:
    """向客户端推送一条 SSE 事件（同时用于流式和日志）。"""
    if wfile is None:
        return
    line = "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"
    try:
        wfile.write(line.encode("utf-8"))
        wfile.flush()
    except Exception:
        pass


def _fetch_chunk_wav(text_chunk: str, lang: str, max_retry: int = 3) -> bytes:
    """对单块文本调一次 /tts（cut0 单段），返回完整 WAV 字节。

    GPT-SoVITS 在流式模式下偶发返回 200 但 body 为空（0 字节），
    多见于模型刚 ready / GPU 忙 / 短文本。此处对“空响应”做指数退避重试，
    避免单个瞬态失败让整段长文本合成中断。
    """
    # 无效输入（纯标点/空/不可见字符）不调用 TTS，也不重试，直接返回空字节交由上层跳过
    if not has_speech(clean_invisible(text_chunk)):
        return b""
    payload = {
        "text": text_chunk,
        "text_lang": lang,
        "ref_audio_path": REF_AUDIO_PATH,
        "prompt_text": PROMPT_TEXT,
        "prompt_lang": PROMPT_LANG,
        "media_type": "wav",
        "streaming_mode": True,               # 与 Agent 侧 TTSService 一致
        "batch_size": 1,
        "text_split_method": PER_CHUNK_SPLIT,  # 单块即单段
    }
    last_err: Exception | None = None
    for attempt in range(1, max_retry + 1):
        conn = http.client.HTTPConnection(TTS_HOST, TTS_PORT, timeout=600)
        try:
            conn.request(
                "POST", "/tts",
                body=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            resp = conn.getresponse()
            if resp.status != 200:
                raw = resp.read().decode("utf-8", "replace")
                # 非 200 属硬错误，不重试
                raise RuntimeError(f"TTS 返回 HTTP {resp.status}: {raw[:300]}")
            raw = resp.read()
            if len(raw) >= 44:          # 合法 WAV 至少含 44 字节 header
                return raw
            # 200 但空/过小响应：疑似瞬态，准备重试
            last_err = RuntimeError(f"TTS 返回空响应（{len(raw)} 字节），疑似瞬态")
            if attempt < max_retry:
                time.sleep(2 ** attempt)   # 退避 2s / 4s / 8s
                continue
        except RuntimeError as exc:
            # HTTP 硬错误直接上抛，不进入重试循环
            raise exc
        finally:
            try:
                conn.close()
            except Exception:
                pass
        break
    # 重试耗尽仍为空响应
    raise RuntimeError(f"重试 {max_retry} 次后仍拿到空音频: {last_err}")


def synthesize_to_file(text: str, lang: str = DEFAULT_TEXT_LANG, fmt: str = "wav",
                       wfile=None) -> dict:
    """核心合成：客户端分块 → 逐块 /tts → 拼接 WAV（可选转 MP4）。

    返回 dict：{path, name, format, size, chunks, chunk_total, elapsed, note}。
    wfile 非空时，过程中推送 SSE 进度事件；为空时静默（供离线脚本复用）。
    """
    text = (text or "").strip()
    fmt = (fmt or "wav").lower()
    if not text:
        raise ValueError("文本为空")
    if not tts_up():
        raise RuntimeError("TTS 服务未启动（连接 127.0.0.1:9880 失败）。")

    chunks = chunk_text(text)
    total = len(chunks)
    _emit(wfile, {"type": "stage", "stage": "send",
                  "text": f"文本分 {total} 段（每段 ≤{MAX_CHARS} 字，逐段合成后拼接，避免漏字）"})

    raw_list = []
    used_texts = []
    params0 = None
    audio_bytes = 0
    chunks_done = 0
    t0 = time.time()
    last_emit = 0.0

    for idx, ch in enumerate(chunks, 1):
        _emit(wfile, {"type": "stage", "stage": "synth",
                      "text": f"合成第 {idx}/{total} 段：{ch[:18]}{'…' if len(ch) > 18 else ''}"})
        raw = _fetch_chunk_wav(ch, lang)
        if len(raw) < 44:
            # 兜底：上游 chunk_text 已过滤纯标点/空块；若仍出现不可合成块，跳过不报错中断整段
            _emit(wfile, {"type": "stage", "stage": "skip",
                          "text": f"跳过第 {idx} 段（不可合成，已忽略）：{ch[:18]}"})
            continue
        try:
            if params0 is None:
                params0, _ = wav_params_and_pcm(raw)
            raw_list.append(raw)
            used_texts.append(ch)
        except Exception as exc:
            raise RuntimeError(f"第 {idx} 段音频解析失败: {exc}")

        audio_bytes += len(raw)
        chunks_done += 1
        now = time.time()
        if wfile and now - last_emit >= 0.15:
            last_emit = now
            _emit(wfile, {
                "type": "progress",
                "chunks": chunks_done,
                "chunk_total": total,
                "bytes": audio_bytes,
                "elapsed": round(now - t0, 1),
            })

    if params0 is None or not raw_list:
        raise RuntimeError("未合成出任何音频。")

    # 平滑拼接：每块淡入淡出 + 按边界类型插入可控停顿，消除块间硬拼接的突兀/爆音
    _concat_n = len(used_texts)
    _emit(wfile, {"type": "stage", "stage": "pack",
                  "text": f"平滑拼接 {_concat_n} 段音频（淡入淡出 + 可控停顿）为完整 WAV"})
    final_wav = concat_chunks_wav(params0, raw_list, used_texts)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    wav_name = f"assistant_{stamp}.wav"
    wav_path = os.path.join(OUT_DIR, wav_name)
    with open(wav_path, "wb") as f:
        f.write(final_wav)

    final_url = f"/outputs/{wav_name}"
    final_name = wav_name
    final_fmt = "wav"
    note = f"长文本已分 {total} 段逐段合成并拼接，避免模型单次推理截断漏字。"

    # MP4 转码（可选）
    if fmt == "mp4":
        _emit(wfile, {"type": "stage", "stage": "encode", "text": "转码为 MP4 (AAC)"})
        mp4_name = f"assistant_{stamp}.mp4"
        mp4_path = os.path.join(OUT_DIR, mp4_name)
        if os.path.exists(FFMPEG):
            try:
                proc = subprocess.run(
                    [FFMPEG, "-y", "-i", wav_path, "-c:a", "aac", "-b:a", "192k", mp4_path],
                    capture_output=True, text=True, timeout=120,
                )
                if proc.returncode == 0 and os.path.exists(mp4_path) and os.path.getsize(mp4_path) > 0:
                    final_url = f"/outputs/{mp4_name}"
                    final_name = mp4_name
                    final_fmt = "mp4"
                    os.remove(wav_path)
                else:
                    note += f" ffmpeg 转码失败，已回退为 WAV: {proc.stderr[:200]}"
            except Exception as exc:
                note += f" ffmpeg 转码异常，已回退为 WAV: {exc}"
        else:
            note += " 未找到 ffmpeg，MP4 不可用，已回退为 WAV。"

    size = os.path.getsize(os.path.join(OUT_DIR, final_name))
    result = {
        "path": os.path.join(OUT_DIR, final_name),
        "url": final_url,
        "name": final_name,
        "format": final_fmt,
        "size": size,
        "chunks": chunks_done,
        "chunk_total": total,
        "elapsed": round(time.time() - t0, 1),
        "note": note,
    }
    _emit(wfile, {
        "type": "done",
        "url": result["url"],
        "name": result["name"],
        "format": result["format"],
        "size": result["size"],
        "chunks": result["chunks"],
        "chunk_total": result["chunk_total"],
        "elapsed": result["elapsed"],
        "note": result["note"],
        "payload": {"text_chunks": chunks, "per_chunk_split": PER_CHUNK_SPLIT, "max_chars": MAX_CHARS},
    })
    return result


def do_tts(wfile, params: dict) -> None:
    """SSE 包装：调用 synthesize_to_file 并把进度推给前端。"""
    text = (params.get("text") or "").strip()
    fmt = (params.get("format") or "wav").lower()
    lang = (params.get("lang") or DEFAULT_TEXT_LANG).lower()

    _emit(wfile, {"type": "stage", "stage": "check", "text": "检查 TTS 服务 (127.0.0.1:9880)"})
    try:
        synthesize_to_file(text, lang=lang, fmt=fmt, wfile=wfile)
    except Exception as exc:
        _emit(wfile, {"type": "error", "message": str(exc)})


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, code, ctype, data: bytes):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            try:
                with open(INDEX_HTML, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            except FileNotFoundError:
                self._send(500, "text/plain; charset=utf-8", b"index.html not found")
            return

        if path == "/api/health":
            self._send(200, "application/json", json.dumps({"ready": tts_up()}).encode("utf-8"))
            return

        if path.startswith("/outputs/"):
            fname = unquote(path[len("/outputs/"):])
            # 防目录穿越
            fname = os.path.basename(fname)
            fpath = os.path.join(OUT_DIR, fname)
            if os.path.isfile(fpath):
                with open(fpath, "rb") as f:
                    data = f.read()
                ctype = "audio/mp4" if fname.lower().endswith(".mp4") else "audio/wav"
                self._send(200, ctype, data)
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")
            return

        self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/tts":
            self._send(404, "application/json", json.dumps({"error": "not found"}).encode("utf-8"))
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            params = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:
            self._send(400, "application/json", json.dumps({"error": str(exc)}).encode("utf-8"))
            return

        # SSE 响应头
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            do_tts(self.wfile, params)
        except Exception as exc:
            _emit(self.wfile, {"type": "error", "message": f"服务端异常: {exc}"})
        finally:
            try:
                self.wfile.flush()
            except Exception:
                pass
            # SSE 是单次响应（发完即结束），必须主动关闭底层连接，
            # 否则客户端 reader 会一直阻塞等待更多事件。
            try:
                self.connection.shutdown(2)  # SHUT_RDWR
            except Exception:
                pass
            try:
                self.connection.close()
            except Exception:
                pass

    def log_message(self, fmt, *args):
        # 精简日志，避免刷屏
        sys.stdout.write("[tts_web] " + (fmt % args) + "\n")


def main():
    for port in WEB_PORTS:
        try:
            server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        except OSError:
            continue
        print(f"TTS 诊断 Web 已启动： http://127.0.0.1:{port}/")
        print(f"  - 参考音频 : {REF_AUDIO_PATH}")
        print(f"  - 输出目录 : {OUT_DIR}")
        print(f"  - ffmpeg   : {FFMPEG} ({'存在' if os.path.exists(FFMPEG) else '缺失'})")
        print("Ctrl+C 退出。")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止。")
            break


if __name__ == "__main__":
    main()
