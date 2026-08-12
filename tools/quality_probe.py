"""一次性探针：对真实 DeepSeek 跑 4 个质量 Case，打印实际候选 + 判定。

仅用于生成质量报告，不进测试套件。用法：
  python tools/quality_probe.py
"""
from __future__ import annotations

import sys
import time

sys.path.insert(0, "tests")  # 让下面的 import 找到 test_extraction_quality
sys.path.insert(0, ".")

import test_extraction_quality as T  # noqa: E402

from config import load_settings  # noqa: E402
from core.llm_client import DeepSeekLLMClient  # noqa: E402
from core.memory import MemoryManager, SQLiteMemoryStore  # noqa: E402
from core.memory.extractor import MemoryExtractor  # noqa: E402
from core.memory.extraction_engine import ExtractionEngine  # noqa: E402


def main() -> int:
    s = load_settings()
    llm = DeepSeekLLMClient(
        api_key=s.api_key, base_url=s.base_url, model=s.model,
        temperature=s.temperature, max_tokens=s.max_tokens, timeout=s.timeout,
    )

    passed = failed = errored = 0
    print("# M4.2 真实抽取质量探针（DeepSeek: %s）\n" % s.model)
    for name, turns, check in T.CASES:
        store = SQLiteMemoryStore(":memory:")
        mgr = MemoryManager(store)
        engine = ExtractionEngine(MemoryExtractor(mgr), llm)
        t0 = time.time()
        try:
            engine.extract([{"role": "user", "content": t} for t in turns])
            cands = mgr.pending_candidates()
            elapsed = time.time() - t0
            try:
                check(mgr, cands)
                verdict, detail = "PASS", ""
                passed += 1
            except AssertionError as e:
                verdict, detail = "FAIL", str(e)
                failed += 1
            except Exception as e:  # noqa: BLE001
                verdict, detail = "ERROR", f"{type(e).__name__}: {e}"
                errored += 1
        except Exception as e:  # noqa: BLE001 - 引擎外层异常（理论上不会，已降级）
            elapsed = time.time() - t0
            cands = []
            verdict, detail = "ERROR", f"{type(e).__name__}: {e}"
            errored += 1

        print("=" * 70)
        print(f"【{name}】 -> {verdict}  ({elapsed:.1f}s)")
        if detail:
            print(f"  理由: {detail}")
        print(f"  输入: {turns}")
        if cands:
            for c in cands:
                print(f"   · [{c['type']}] imp={c['importance']} cf={c['confidence']}  {c['content']!r}")
                if c.get("reason"):
                    print(f"       原因: {c['reason']}")
        else:
            print("   · (无候选)")
        # Gate 不变式核对
        long_term = mgr.list_memories()
        print(f"   Gate: long_term 正表条数 = {len(long_term)}  (必须=0)")

    print("\n" + "=" * 70)
    print(f"汇总: {passed} PASS / {failed} FAIL / {errored} ERROR")
    return 1 if (failed or errored) else 0


if __name__ == "__main__":
    raise SystemExit(main())
