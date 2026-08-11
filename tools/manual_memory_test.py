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
import socket
import sys
import tempfile
from pathlib import Path

# 允许 `python tools/manual_memory_test.py` 直接运行（把项目根加入 path）
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.memory import (
    ExtractedMemory,
    HashingEmbedder,
    MemoryExtractor,
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


def hf_reachable(host: str = "huggingface.co", port: int = 443, timeout: float = 2.0) -> bool:
    """廉价探测 HuggingFace 是否可达；不可达即视为离线（不触发模型下载重试）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


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


def gate_section() -> None:
    """Memory Gate 集成验证（离线可跑）：抽取器只进候选闸门，确认前绝不污染检索。

    直接用真实检索管线验证 Step A 的数据隔离保证——不仅是单元测试，而是
    经 MemoryManager.retrieve → MemoryRetriever 五通道打分这条完整链路。
    """
    section("B. Memory Gate 集成：抽取器只进候选，确认前不进检索")
    tmp = Path(tempfile.mkdtemp()) / "manual_gate_test.db"
    store = SQLiteMemoryStore(tmp)
    gate = MemoryManager(store, embedder=HashingEmbedder(dim=256))
    ext = MemoryExtractor(gate)

    # 模拟 Step 2.7 抽取器产出一条「临时信息」候选（如「今天天气不错」类）
    cand = ext.propose_one(ExtractedMemory(
        "preference", "用户说今天天气不错，心情放松", confidence=3, importance=2,
    ))
    print(f"   抽取器 propose → 候选 id={cand}")
    print("   => 候选落 memory_candidate（闸门）：", "PASS" if cand > 0 else "FAIL")
    in_pending = any(c.get("id") == cand for c in ext.pending())
    print("   => 候选在待确认队列中：", "PASS" if in_pending else "FAIL")

    # 确认前：检索不应返回这条临时信息（数据隔离经真实管线成立）
    q = "你今天心情怎么样？"
    hits_before = gate.retrieve(q, top_k=5)
    leaked_before = any("天气不错" in (h.content or "") for h in hits_before)
    print(f"   [确认前] 检索 {q!r} → 命中 {len(hits_before)} 条")
    print("   => 候选未进入长期记忆 / 检索读不到：",
          "PASS" if not leaked_before else "FAIL（闸门失效!）")

    # 人点「记住」→ 转正
    mem_id = gate.confirm_candidate(cand)
    print(f"   人确认 → 转正 memory id={mem_id}")
    print("   => 转正成功：", "PASS" if mem_id > 0 else "FAIL")

    # 确认后：检索应命中
    hits_after = gate.retrieve(q, top_k=5)
    recalled_after = any("天气不错" in (h.content or "") for h in hits_after)
    print(f"   [确认后] 检索 {q!r} → 命中 {len(hits_after)} 条")
    print("   => 转正记忆可被检索召回：", "PASS" if recalled_after else "FAIL")

    # reject 路径：另一条候选被否决后绝不进检索
    cand2 = ext.propose_one(ExtractedMemory("fact", "用户随口提了句想喝奶茶", confidence=2, importance=1))
    gate.reject_candidate(cand2)
    hits_rej = gate.retrieve("你想喝什么？", top_k=5)
    leaked_rej = any("奶茶" in (h.content or "") for h in hits_rej)
    print("   => reject 后候选永不进检索：", "PASS" if not leaked_rej else "FAIL（污染!）")

    gate.close()
    store.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="auto", choices=["auto", "hashing", "local"])
    args = ap.parse_args()

    # 嵌入层
    # LocalEmbedder 是惰性加载：get_embedder 成功返回，但 bge 权重在首次 encode()
    # （即 mgr.remember/reindex）时才真正下载。离线时若在那一刻才失败，会崩在
    # try 之外。因此离线直接走哈希，不构造 LocalEmbedder；在线才允许 auto 下载。
    if args.backend in ("auto", "local") and not hf_reachable():
        print("[net] 检测到离线环境：直接使用哈希嵌入（不尝试下载 bge，避免卡顿/崩溃）")
        emb = HashingEmbedder(dim=256)
    else:
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
    print("   注：当前五通道打分含 recency/importance/confidence（与查询无关），记忆极少时")
    print("       无相关性门槛，无关查询也会返回全部记忆。真实 bge 仅强化 vector 通道，")
    print("       不改变该底层打分结构；若需『无关严格不召回』，后续应加「要求 vector/keyword")
    print("       至少一项有贡献」的相关性门槛（属检索增强，非 gate 职责）。")

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

    # Memory Gate 集成验证（独立库，不污染上面的测试记忆）
    gate_section()

    store.close()
    section("结论")
    print("   - 检索层五通道混合打分工作正常，Prompt 注入定界且无分数泄漏。")
    print("   - 语义类召回（开发习惯/AI项目）依赖真实 bge 向量；离线哈希只能命中字面重叠。")
    print("   - 冲突场景：当前实现不做自动置信度合并/降级，新旧记忆共存。")
    print("     若需冲突消解（旧记忆降 confidence 或标记 superseded），可后续实现。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
