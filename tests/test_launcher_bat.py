"""start_assistant.bat 防回归测试（2026-08-17 冷启动失败事故）。

事故根因：cmd 的 `if` 会把行内剩余全部内容（含 && / & 链）吞进条件体，
`if not exist logs mkdir logs && python api_v2.py ...` 在 logs/ 已存在时
整条链都不执行——TTS 进程从未启动，启动器空等 600s 后以纯文字模式降级。
此前从未暴露，是因为 TTS 总是被提前手动启动，bat 一直走「复用在线」路径。

本测试把三条硬约束固化，防止将来被「改回简洁写法」：
1. 编码必须是 GBK/ANSI 无 BOM（UTF-8+BOM 与 chcp 方案均已实证有毒，见 docs/11）
2. TTS 启动行的 mkdir 守卫必须带括号（终止 if 条件体）
3. 全文件不得再出现会被 if 吞掉的 `... && python` 裸链写法
"""
from pathlib import Path

_BAT = Path(__file__).resolve().parents[1] / "start_assistant.bat"


def _read_bat() -> str:
    data = _BAT.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), "bat 不得带 UTF-8 BOM（会粘坏首行）"
    return data.decode("gbk")  # 解不开即失败：必须保持 ANSI/GBK


def test_bat_encoding_gbk_no_bom():
    _read_bat()  # 能以 GBK 解码 + 无 BOM 即通过


def test_tts_launch_line_has_parenthesized_guard():
    bat = _read_bat()
    start_lines = [l for l in bat.splitlines() if 'start "GPT-SoVITS TTS"' in l]
    assert len(start_lines) == 1
    line = start_lines[0]
    assert "(if not exist logs mkdir logs) & " in line, (
        "mkdir 守卫必须括号化：cmd 的 if 会把 &&/& 链整个吞进条件体，"
        "logs 已存在时 python 根本不会执行（2026-08-17 冷启动事故根因）"
    )


def test_no_swallowed_and_chain_into_python():
    bat = _read_bat()
    for line in bat.splitlines():
        if "api_v2.py" in line and "cmd /k" in line:
            assert "logs mkdir logs &&" not in line.replace("(", "").replace(")", "")
