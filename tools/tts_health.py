"""M4 启动脚本用的 TTS 健康检查 / 等待就绪工具。

设计约束：
- 仅用标准库（urllib / socket / argparse / time / sys），不依赖任何第三方包，
  因此随便哪个 Python（Agent venv / GPT-SoVITS runtime）都能跑。
- 「就绪」的判定：GET http://host:port/ 能拿到任意 HTTP 响应（含 404）即视为
  服务在监听、端口已绑定。GPT-SoVITS 的 api_v2.py 一旦 uvicorn 起来，根路径
  就会响应（哪怕 /tts 还在加载模型）——这正是「可以开始等 / 复用」的信号。
- 与 TTSService.health_check 的语义保持一致（任何响应即 up，连接失败即 down）。

退出码：0 = 就绪；1 = 超时未就绪。供 start_assistant.bat 用 errorlevel 判断。
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request


def probe(host: str, port: int, timeout: float = 2) -> bool:
    """探测一次：根路径能否拿到响应。连接失败/超时返回 False。"""
    url = f"http://{host}:{port}/"
    try:
        # 任意响应（含 404）都说明服务在监听 -> True
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        # 404 / 5xx 也是「服务在」的信号
        return True
    except Exception:
        return False


def wait_ready(host: str, port: int, timeout: int = 60, interval: int = 2) -> bool:
    """轮询直到就绪或超时。返回是否就绪。"""
    if timeout < 0:
        timeout = 0
    deadline = time.time() + timeout
    while True:
        if probe(host, port):
            return True
        if time.time() >= deadline:
            return False
        # 还没到 deadline 才睡；避免最后一圈多余等待
        remaining = deadline - time.time()
        time.sleep(min(interval, remaining) if remaining > 0 else 0)
        if time.time() >= deadline:
            # 最后一次睡眠后仍未就绪
            return probe(host, port)


def main() -> int:
    p = argparse.ArgumentParser(description="GPT-SoVITS TTS 健康检查 / 等待就绪")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9880)
    p.add_argument("--timeout", type=int, default=60, help="最长等待秒数（0 = 只探一次）")
    p.add_argument("--interval", type=int, default=2, help="轮询间隔秒数")
    args = p.parse_args()

    ok = wait_ready(args.host, args.port, args.timeout, args.interval)
    if ok:
        print(f"[TTS] ready on {args.host}:{args.port}")
        return 0
    print(f"[TTS] NOT ready on {args.host}:{args.port} after {args.timeout}s")
    return 1


if __name__ == "__main__":
    sys.exit(main())
