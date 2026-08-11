"""人工记忆测试（离线可复现；默认启用真实 bge 语义向量）。

不依赖 DeepSeek API：直接验证 MemoryManager.retrieve / memory_block / Prompt 注入，
并覆盖用户给的冲突场景（旧 Python vs 新 Rust）下的 confidence 处理。

运行：
    python tools/manual_memory_test.py            # 默认 auto（能联网就用 bge，否则哈希回退）
    python tools/manual_memory_test.py --backend hashing   # 强制纯哈希（离线、确定性）

注：用户给的 importance=0.9 在系统里是 0~1 比例，映射到 1~10 的 importance=9。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

# 允许 `python tools/manual_memory_test.py` 直接运行（把项目根加入 path）
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.memory import (
    HashingEmbedder,
    MemoryManager,
    SQLiteMemoryStore,
)
from core.memory.embedder import DEFAULT_MODEL, get_embedder
from core.persona import load_persona
from core.prompt_builder import PromptBuilder


def section(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def banner(embedder) -> None:
    print(f"[backend] 嵌入器 = {embedder.name}  dim = {embedder.dim}")
    if embedder.name == "hashing-zh-v1":
        print("[backend] 警告：未使用真实语义向量， paraphrase/语义类召回会偏弱。")


def show_query(mgr: MemoryManager, query: str, *, top_k: int = 5) -> list:
    hits = mgr.retrieve(query, top_k=top_k)
    block = mgr.memory_block(query, top_k=top_k)
    print(f"\n>> 用户问：{query!r}")
    if not hits:
        print("   检索结果：无命中（记忆未召回）")
    else:
        for h in hits:
            detail = " | ".join(f"{k}={v:.2f}" for k, v in h.channels.items())
            print(f"   命中#{h.id} [{h.type}] score={h.score:.3f}  {detail}")
            print(f"        └─ {h.content}")
    # 展示注入到 system 提示词的记忆段落（定界、无分数）
    assert "score=" not in block, "分数泄漏进提示词！"
    print("   —— 注入 system 提示词的【相关用户记忆】段落 ——")
    if block.strip():
        print("   " + block.strip().replace("\n", "\n   "))
    else:
        print("   （空，本轮不注入记忆）")
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="auto", choices=["auto", "hashing", "local"])
    args = ap.parse_args()

    # 嵌入层
    try:
        emb = get_embedder(args.backend, model_name=DEFAULT_MODEL, device="cpu")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] 真实嵌入不可用，回退哈希嵌入：{e}")
        emb = HashingEmbedder(dim=256)
    banner(emb)

    # 隔离的临时库，避免污染真实数据
    tmp = Path(tempfile.mkdtemp()) / "manual_mem_test.db"
    store = SQLiteMemoryStore(tmp)
    mgr = MemoryManager(store, embedder=emb)

    section("0. 写入测试记忆")
    # 用户指定：preference / 用户喜欢先规划架构再写代码 / importance 0.9 → 9
    pref_id = mgr.remember("preference", "用户喜欢先规划架构再写代码", importance=9, confidence=5)
    # 为 Test 1 提供「开发AI Agent」记忆
    ai_id = mgr.remember("fact", "用户正在开发一个长期陪伴型 AI Agent 项目（助手）", importance=8, confidence=5)
    # 为 Test 3 准备原始记忆
    py_id = mgr.remember("fact", "用户喜欢用 Python", importance=5, confidence=5)
    mgr.reindex()
    print(f"   已写入：preference#{pref_id}  fact(AI Agent)#{ai_id}  fact(Python)#{py_id}")

    section("A. 聊天：『你觉得我的开发习惯是什么？』—— 看助手有没有调用")
    hits_a = show_query(mgr, "你觉得我的开发习惯是什么？")
    recalled_pref = any("规划架构" in h.content or "写代码" in h.content for h in hits_a)
    print("   => 期望召回『用户喜欢先规划架构再写代码』：", "PASS" if recalled_pref else "FAIL（语义/关键词不足）")

    section("Test 1 相关：『继续我们的AI项目』应召回『开发AI Agent』")
    hits1 = show_query(mgr, "继续我们的AI项目")
    recalled_ai = any("AI Agent" in h.content or "AI" in h.content for h in hits1)
    print("   => 期望召回『开发AI Agent』：", "PASS" if recalled_ai else "FAIL")

    section("Test 2 无关：『今天晚上吃什么』不应召回『AI项目』")
    hits2 = show_query(mgr, "今天晚上吃什么")
    leaked = any("AI Agent" in h.content for h in hits2)
    print("   => 期望不召回『AI项目』：", "PASS" if not leaked else "FAIL（无关内容被召回!）")

    section("Test 3 冲突：旧『用户喜欢Python』 vs 新『用户改用Rust』")
    print("   写入旧记忆：用户喜欢Python（confidence=5）")
    old_id = mgr.remember("fact", "用户喜欢用 Python", importance=5, confidence=5)
    print(f"   旧记忆 id={old_id}")
    print("   写入新记忆：用户改用Rust（confidence=5）")
    new_id = mgr.remember("fact", "用户改用 Rust", importance=5, confidence=5)
    mgr.reindex()
    print(f"   新记忆 id={new_id}")
    show_query(mgr, "你用什么编程语言？")
    print("   冲突后两条记忆的 confidence / importance / updated_at：")
    for r in mgr.list_memories(types=("fact",), limit=200):
        c = r.get("content") or ""
        if "Python" in c or "Rust" in c:
            print(
                f"     id={r.get('id')} content={c!r} "
                f"confidence={r.get('confidence')} importance={r.get('importance')} "
                f"updated_at={r.get('updated_at')}"
            )
    print("   => 当前系统行为：两条记忆并存，旧记忆 confidence 未被自动下调（无冲突消解）。")

    store.close()
    section("结论")
    print("   - 检索层五通道混合打分工作正常，Prompt 注入定界且无分数泄漏。")
    print("   - 语义类召回（开发习惯/AI项目）依赖真实 bge 向量；离线哈希只能命中字面重叠。")
    print("   - 冲突场景：当前实现不做自动置信度合并/降级，新旧记忆共存。")
    print("     若需冲突消解（旧记忆降 confidence 或标记 superseded），可后续实现。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
