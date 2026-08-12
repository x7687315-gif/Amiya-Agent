"""M4.2 真实抽取质量测试集（最重要）。

直接打真实 DeepSeek，验证「记忆抽取 + 候选闸门」在真实 LLM 下不污染记忆：

  Case 1  应记：身份 + 目标
  Case 2  不记：瞬时吐槽
  Case 3  情绪不能固化成事实（特质）
  Case 4  角色信息隔离：助手的偏好 ≠ 用户的偏好

以及一条**无需联网**的结构不变式测试：
  test_gate_offline_never_writes_long_term —— 无论 LLM 产出什么，
  ExtractionEngine 永远只写 memory_candidate 闸门，绝不直写 long_term 正表。

运行：
  需要 DEEPSEEK_API_KEY 与网络。无 key 时整组（含 Case 1-4）自动 skip。
  - pytest：  python -m pytest tests/test_extraction_quality.py -v
  - 直跑报告：python tests/test_extraction_quality.py

设计意图：本项目的卖点不是「写了个数据库」，而是「设计了一个不会污染人格与
用户记忆的长期陪伴 Agent」。这些用例就是那个体感的验收门槛。
"""
from __future__ import annotations

import os
import sys

import pytest

from core.memory import MemoryManager, SQLiteMemoryStore
from core.memory.extractor import MemoryExtractor
from core.memory.extraction_engine import ExtractionEngine

HAS_KEY = bool((os.getenv("DEEPSEEK_API_KEY") or "").strip())
requires_key = pytest.mark.skipif(not HAS_KEY, reason="需要 DEEPSEEK_API_KEY 才能跑真实抽取")


# ---------- 离线结构不变式（Gate）：不需要 key / 网络 ----------

class _FakeChat:
    """离线替身：返回固定 JSON，验证引擎绝不直写长期记忆。"""

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def chat(self, system, history, *, temperature=None, max_tokens=None) -> str:
        return self.payload

    def stream_chat(self, system, history):  # 抽取路径不调，留空实现满足协议
        return iter([])


def test_gate_offline_never_writes_long_term():
    """无论 LLM 抽出了什么，ExtractionEngine 只能落 candidate，绝不落 long_term。"""
    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    fake = _FakeChat(
        '[{"type":"fact","content":"用户喜欢晴天","importance":5,'
        '"confidence":4,"reason":"测试用"}]'
    )
    engine = ExtractionEngine(MemoryExtractor(mgr), fake)
    ids = engine.extract([{"role": "user", "content": "今天天气不错"}])

    assert ids, "应至少产出 1 条候选"
    # 关键不变式：长期记忆正表必须始终为空（人工未确认前绝不污染）
    assert mgr.list_memories() == [], "抽取引擎绝不能直接写长期记忆正表"
    cands = mgr.pending_candidates()
    assert len(cands) == 1 and cands[0]["type"] == "fact"


# ---------- 真实抽取用例 ----------

def _build_engine():
    from config import load_settings
    from core.llm_client import DeepSeekLLMClient

    s = load_settings()
    store = SQLiteMemoryStore(":memory:")
    mgr = MemoryManager(store)
    llm = DeepSeekLLMClient(
        api_key=s.api_key,
        base_url=s.base_url,
        model=s.model,
        temperature=s.temperature,
        max_tokens=s.max_tokens,
        timeout=s.timeout,
    )
    return mgr, ExtractionEngine(MemoryExtractor(mgr), llm)


def _hit(text: str, keywords) -> bool:
    return any(kw in text for kw in keywords)


def _assert_gate(mgr: MemoryManager) -> None:
    """结构保证：抽取后长期记忆正表必须为空。"""
    assert mgr.list_memories() == [], "抽取绝不能自动写入长期记忆正表"


def _run(turns) -> "tuple[MemoryManager, list]":
    mgr, engine = _build_engine()
    engine.extract([{"role": "user", "content": t} for t in turns])
    return mgr, mgr.pending_candidates()


# --- 各 Case 的断言 ---

def _check_case1(mgr, cands):
    _assert_gate(mgr)
    facts = [c for c in cands if c["type"] == "fact"]
    goals = [c for c in cands if c["type"] == "goal"]
    assert facts, f"Case1: 未产出任何 fact 候选: {cands}"
    assert any(_hit(c["content"], ["大三", "学生", "在读", "大学"]) for c in facts), (
        f"Case1: 未抽出『大三学生』事实: {cands}"
    )
    assert goals, f"Case1: 未产出任何 goal 候选: {cands}"
    assert any(_hit(c["content"], ["AI Agent", "实习", "agent"]) for c in goals), (
        f"Case1: 未抽出『准备 AI Agent 实习』目标: {cands}"
    )


def _check_case2(mgr, cands):
    _assert_gate(mgr)
    assert cands == [], f"Case2: 瞬时吐槽不应产生任何候选: {cands}"


def _check_case3(mgr, cands):
    _assert_gate(mgr)
    # 禁止把情绪固化成『长期/性格』事实
    forbidden = [
        "长期焦虑", "经常焦虑", "用户焦虑", "性格焦虑", "易焦虑",
        "一直焦虑", "长期压力大", "总是焦虑", "天生焦虑",
    ]
    for c in cands:
        assert c["type"] != "fact" or not _hit(c["content"], forbidden), (
            f"Case3: 情绪被误固化为特质事实: {c}"
        )
    # 允许：event 类（如『近期学习压力较大』）或干脆不记——总之不能是特质事实


def _check_case4(mgr, cands):
    _assert_gate(mgr)
    # 角色（助手）的偏好绝不能张冠李戴成用户（用户）的偏好
    for c in cands:
        text = c["content"]
        if "胡萝卜" in text:
            assert "助手" in text, f"Case4: 胡萝卜相关候选未指明是助手的偏好: {c}"
            assert not _hit(
                text, ["用户喜欢", "用户喜欢", "你喜欢", "我（用户）喜欢"]
            ), f"Case4: 角色偏好被错误归到用户头上: {c}"


CASES = [
    ("Case1 应记：身份+目标", ["我叫小明，今年大三，正在准备 AI Agent 方向实习。"], _check_case1),
    ("Case2 不记：瞬时吐槽", ["今天下雨了，好烦。"], _check_case2),
    ("Case3 情绪非事实", ["最近压力特别大。"], _check_case3),
    ("Case4 角色信息隔离", ["助手喜欢胡萝卜。"], _check_case4),
]


@pytest.mark.parametrize("name,turns,check", CASES, ids=[c[0] for c in CASES])
@requires_key
def test_real_extraction(name, turns, check):
    mgr, cands = _run(turns)
    check(mgr, cands)


# ---------- 直跑报告（python tests/test_extraction_quality.py） ----------

if __name__ == "__main__":
    if not HAS_KEY:
        print("SKIP: 未设置 DEEPSEEK_API_KEY，无法运行真实抽取质量测试。")
        print("      请先 export DEEPSEEK_API_KEY=sk-xxx 再运行。")
        sys.exit(0)

    from config import load_settings
    from core.llm_client import DeepSeekLLMClient

    s = load_settings()
    passed = failed = 0
    for name, turns, check in CASES:
        try:
            store = SQLiteMemoryStore(":memory:")
            mgr = MemoryManager(store)
            llm = DeepSeekLLMClient(
                api_key=s.api_key, base_url=s.base_url, model=s.model,
                temperature=s.temperature, max_tokens=s.max_tokens, timeout=s.timeout,
            )
            engine = ExtractionEngine(MemoryExtractor(mgr), llm)
            engine.extract([{"role": "user", "content": t} for t in turns])
            check(mgr, mgr.pending_candidates())
            print(f"[PASS] {name}")
            passed += 1
        except AssertionError as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] {name}: {type(e).__name__}: {e}")
            failed += 1

    print(f"\n真实抽取质量结果: {passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
