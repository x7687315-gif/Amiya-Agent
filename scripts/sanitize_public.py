"""公开仓库脱敏工具：把内部版本复制过来的代码做通用化改写。

为什么需要脚本而不是手改：迁移会反复发生（每个阶段一次），
手改容易漏，且无法复查。规则集中在这里，改动可审计。

注意：本文件里的敏感词一律用 **Unicode 转义**书写。
第一版直接写了中文，结果脚本把自己的替换表也改掉了
（("阿米娅","助手") → ("助手","助手")），自我脱敏导致规则失效。

用法::

    python scripts/sanitize_public.py            # 改写 + 打印残留风险
    python scripts/sanitize_public.py --check    # 只检查不改写
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SUFFIXES = {".py", ".md", ".yaml", ".yml", ".txt", ".json"}
SKIP_DIRS = {".git", ".venv", "__pycache__", "data", "logs", "models", "voices", ".pytest_cache"}

# \u963f\u7c73\u5a05 = IP 角色名；\u535a\u58eb = 称呼；\u660e\u65e5\u65b9\u821f = IP 名
_IP_NAME = "\u963f\u7c73\u5a05"
_ADDRESS = "\u535a\u58eb"
_IP_TITLE = "\u660e\u65e5\u65b9\u821f"
_IP_ORG = "\u7f57\u5fb7\u5c9b"

REPLACEMENTS = [
    ("\u300a" + _IP_TITLE + "\u300b\u4e2d\u7684", ""),   # 《…》中的
    (_IP_NAME, "\u52a9\u624b"),                          # 角色名 → 助手
    (_ADDRESS, "\u7528\u6237"),                          # 称呼 → 用户
    (_IP_ORG, "\u672c\u5730"),                           # 组织名 → 本地
    ("amiya.db", "assistant.db"),
]

RISK_PATTERNS = [
    (r"C:\\Users\\[^\\\s]+", "绝对路径（Windows 用户目录）"),
    (r"/c/amiya-" + "agent", "私有仓库路径"),
    (r"sk-[A-Za-z0-9]{16,}", "疑似 API Key"),
    (r"[A-Za-z0-9]{4,}-gif", "疑似个人 GitHub 账号"),  # 不写入真实账号
    ("86" + "159", "本机用户名"),
]


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == "sanitize_public.py":  # 自身含检测规则，跳过
            continue
        yield path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只检查，不写回")
    ap.add_argument("--root", default=".", help="仓库根目录")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    changed, risks = [], []

    for path in iter_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        new = text
        for old, rep in REPLACEMENTS:
            new = new.replace(old, rep)
        if new != text:
            changed.append(path.relative_to(root).as_posix())
            if not args.check:
                path.write_text(new, encoding="utf-8")
        for pattern, label in RISK_PATTERNS:
            for m in re.finditer(pattern, new):
                risks.append((path.relative_to(root).as_posix(), label, m.group(0)[:40]))

    print(f"改写文件数：{len(changed)}" + ("（--check 未写回）" if args.check else ""))
    for c in changed[:15]:
        print("  ", c)
    if len(changed) > 15:
        print(f"  … 其余 {len(changed) - 15} 个")

    if risks:
        print(f"\n残留风险 {len(risks)} 处（需人工确认）：")
        for p, label, sample in risks[:20]:
            print(f"  {p}: {label} → {sample}")
        sys.exit(1)
    print("\n无残留风险标记。")


if __name__ == "__main__":
    main()
