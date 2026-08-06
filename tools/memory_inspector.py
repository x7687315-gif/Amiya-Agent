"""记忆自检工具（Step 2.4 验证）。

离线可用（不需要 DeepSeek API），用于确认记忆系统的四件事是否打通：
  1. 写入 Memory   —— MemoryManager.remember / propose + confirm
  2. 检索 Memory   —— MemoryManager.retrieve 并打印五通道分项
  3. Agent 读取    —— 构造 Agent 并对话，捕获它检索到的命中
  4. Prompt 注入   —— 捕获 Agent 实际发给 LLM 的 system 提示词，确认含【相关记忆】

用法（项目根目录）：
    python tools/memory_inspector.py                      # 用临时库，不污染真实数据
    python tools/memory_inspector.py --db data/assistant.db   # 在真实库上验证
    python tools/memory_inspector.py --backend auto       # 尝试用本地 bge 模型（需装 sentence-transformers）
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

# 允许 `python tools/memory_inspector.py` 直接运行（把项目根加入 path）
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.agent import Agent  # noqa: E402
from core.memory import MemoryManager, SQLiteMemoryStore, get_embedder  # noqa: E402
from core.persona import load_persona  # noqa: E402


class CaptureLLM:
    """桩 LLM：记录收到的 system 提示词与 history，不真正请求网络。"""

    def __init__(self, reply: str = "（自检占位回复）") -> None:
        self.reply = reply
        self.last_system = ""
        self.last_history: list = []

    def stream_chat(self, system: str, history: list) -> "Iterator[str]":
        self.last_system = system
        self.last_history = history
        yield self.reply


def _section(title: str) -> None:
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(description="助手记忆系统自检（Step 2.4）")
    parser.add_argument("--db", help="SQLite 路径；省略则用临时库")
    parser.add_argument(
        "--backend",
        default="hashing",
        help="嵌入后端：hashing（默认，离线）/ auto / local",
    )
    args = parser.parse_args()

    if args.db:
        db_path = args.db
    else:
        # delete=False：自检库保留下来便于用户事后查看（用后即弃由用户决定）
        tmp = tempfile.NamedTemporaryFile(suffix=".db", prefix="assistant_inspect_", delete=False)
        db_path = tmp.name
        tmp.close()
    embedder = get_embedder(backend=args.backend)
    print(f"嵌入后端：{embedder.name}（dim={embedder.dim}）")
    print(f"数据库：{db_path}")

    store = SQLiteMemoryStore(db_path)
    mgr = MemoryManager(store, embedder=embedder)

    # ---------- 1) 写入 Memory ----------
    _section("1) 写入 Memory")
    mgr.remember("fact", "用户正在准备大学入学考试", importance=8, confidence=4)
    mgr.remember("preference", "用户喜欢在晚上安静地看书", importance=5, confidence=4)
    mgr.remember("fact", "用户对猫毛过敏", importance=7, confidence=5)
    cid = mgr.propose("goal", "用户想在今年学会弹吉他", importance=6)
    assert mgr.pending_candidates(), "候选应当已写入"
    mid_goal = mgr.confirm_candidate(cid)
    print(f"  直接写入 fact/preference 共 3 条；候选转正 goal 1 条（mem_id={mid_goal}）")
    n = mgr.reindex()
    print(f"  reindex 补齐向量 {n} 条")
    print(f"  当前记忆总数：{len(mgr.list_memories())}")

    # ---------- 2) 检索 Memory ----------
    _section("2) 检索 Memory（五通道分项）")
    hits = mgr.retrieve("用户最近在忙什么学习和考试", top_k=5)
    if not hits:
        print("  （未命中——检查嵌入后端是否可用）")
    for h in hits:
        print("  " + h.explain())
    print(f"  命中 {len(hits)} 条")

    # ---------- 3) Agent 读取 Memory ----------
    _section("3) Agent 读取 Memory")
    captured: list = []
    llm = CaptureLLM()
    agent = Agent(
        persona=load_persona(),
        llm=llm,
        memory=mgr,
        memory_top_k=5,
        on_retrieval=lambda hs: captured.extend(hs),
    )
    list(agent.reply("我最近是不是特别忙？"))
    print(f"  Agent 本轮检索命中 {len(captured)} 条：")
    for h in captured:
        print("    " + h.explain())

    # ---------- 4) Prompt 注入效果 ----------
    _section("4) Prompt 注入效果（Agent 实际发给 LLM 的 system 提示词）")
    sys_prompt = llm.last_system
    idx = sys_prompt.find("【相关记忆】")
    if idx == -1:
        print("  （本轮未注入记忆——检索可能为空，或记忆与问题不相关）")
    else:
        end = sys_prompt.find("\n\n", idx)
        block = sys_prompt[idx : end if end != -1 else idx + 600]
        print(block)

    _section("自检完成")
    print("  若 1~4 均有输出且 4 出现了【相关记忆】段落，说明记忆读取闭环已打通。")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
