"""M4 测试：TTS 健康检查 / 等待就绪逻辑（tools/tts_health.py）。

bat 本身是 Windows cmd 脚本、依赖 GUI/CUDA，无法在本环境直接跑；
这里测的是 bat 所依赖的「就绪判定 + 超时」核心逻辑，且用真实本地服务器
与真实死端口验证，等价于覆盖了 M4 的 ③/④ 步。

覆盖：
- 服务在监听（含 404）→ wait_ready 返回 True
- 死端口 → wait_ready 在超时内返回 False
- 连接拒绝 / HTTP 404 两种响应都被 probe 判为「在」
"""
from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from tools.tts_health import probe, wait_ready


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(port: int, stop: threading.Event):
    """启动本地回显服务器并立即返回；stop.set() 后自动 shutdown。"""
    class _H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):  # 静默
            pass

    srv = HTTPServer(("127.0.0.1", port), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def _watchdog():
        stop.wait()
        srv.shutdown()

    threading.Thread(target=_watchdog, daemon=True).start()
    return srv


def test_probe_true_when_listening():
    port = _free_port()
    stop = threading.Event()
    _start_server(port, stop)
    try:
        assert probe("127.0.0.1", port, timeout=2) is True
    finally:
        stop.set()


def test_wait_ready_true_against_live_server():
    port = _free_port()
    stop = threading.Event()
    _start_server(port, stop)
    try:
        # 应在极短时间内判定就绪
        assert wait_ready("127.0.0.1", port, timeout=5, interval=0.2) is True
    finally:
        stop.set()


def test_wait_ready_false_on_dead_port():
    # 一个几乎不可能被占用的高端口；超时内应返回 False
    dead = 61337
    start = time.time()
    assert wait_ready("127.0.0.1", dead, timeout=2, interval=0.5) is False
    # 不应明显超出超时
    assert time.time() - start < 4


def test_probe_true_on_http_404():
    # GPT-SoVITS api_v2.py 根路径返回 404，但仍在监听 -> 应判为就绪
    import urllib.error

    class _Resp:
        status = 404

    with patch(
        "tools.tts_health.urllib.request.urlopen",
        side_effect=urllib.error.HTTPError(None, 404, "nf", None, None),
    ):
        assert probe("127.0.0.1", 9880, timeout=1) is True


def test_probe_false_on_connection_refused():
    import urllib.error

    with patch(
        "tools.tts_health.urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        assert probe("127.0.0.1", 9880, timeout=1) is False
