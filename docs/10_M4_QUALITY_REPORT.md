# M4.2 真实抽取质量报告（DeepSeek 实跑）

- 日期：2026-08-12
- 模型：`deepseek-chat`（DeepSeek OpenAI 兼容接口）
- 抽取配置：`temperature=0.0`（引擎注入）、`max_tokens=1024`，手动「整理记忆」窗口 = `manual_extract_window=20` 轮
- 抽取提示词：`EXTRACTION_SYSTEM_PROMPT`（独立抽取助手，不注入助手人格，见 `core/memory/extraction_engine.py`）
- 运行方式：`python -m pytest tests/test_extraction_quality.py -v`（或 `python tools/quality_probe.py` 看详细候选）

## 结论摘要

| 项 | 结果 |
|----|------|
| 真实 Case 1–4 | **4 / 4 PASS** |
| 离线 Gate 不变式（绝不直写 long_term） | **PASS** |
| Memory Gate 污染 | **0 例**（每 case `long_term=0`） |
| 总耗时 | 4 个真实调用合计 ~3.7s（单 call 0.7–1.4s） |

> 核心卖点验证通过：**真实 LLM 下，抽取引擎只产出候选、绝不自动污染长期记忆正表**。

## 逐 Case 实际输出

### Case 1 — 应记：身份 + 目标  ✅ PASS（1.4s）
输入：`我叫小明，今年大三，正在准备 AI Agent 方向实习。`

实际候选：
- `[goal] imp=8 cf=5` “用户正在准备 AI Agent 方向的实习。” — 原因：用户当前重要目标，影响后续对话主题与帮助方向。
- `[fact] imp=7 cf=5` “用户叫小明，今年大三。” — 原因：用户基本身份信息，长期稳定。

Gate：`long_term=0` ✅

### Case 2 — 不记：瞬时吐槽  ✅ PASS（0.7s）
输入：`今天下雨了，好烦。`

实际候选：**（无）** — 正确未把瞬时情绪/天气当记忆。

Gate：`long_term=0` ✅

### Case 3 — 情绪非事实  ✅ PASS（0.9s）
输入：`最近压力特别大。`

实际候选：**（无）** — 未固化成「长期/性格焦虑」类 fact，符合验收（允许 event 或不记；本例选择不记，保守无污染）。

Gate：`long_term=0` ✅

### Case 4 — 角色信息隔离  ✅ PASS（0.7s）
输入：`助手喜欢胡萝卜。`

实际候选：**（无）** — 未把助手的偏好张冠李戴成用户（用户）偏好，角色信息未污染用户记忆。

Gate：`long_term=0` ✅

### 离线 Gate 不变式  ✅ PASS
`test_gate_offline_never_writes_long_term`：用 FakeLLM 强返一条 fact 候选，断言 `ExtractionEngine` 只写 `memory_candidate` 闸门、`list_memories()==[]`。无需联网，永远成立。

## 给 M4.3 决策的质量观察

整体抽取质量**已经很好且零污染**，Gate 结构保证到位。若要进入 M4.3 优化，可考虑以下点（均非阻塞，按价值排序）：

1. **Case 3 的「不记录」取舍**（设计权衡，非 bug）
   当前情绪类输入直接不记。作为陪伴 Agent，把「近期学习压力较大」以 `event`（低 importance）记录，可能更利于后续共情。
   → M4.3 可在 prompt 中允许「情绪/状态」以 `event` + 低权重记录，而非一律丢弃。风险：记录过多。

2. **候选 confidence 普遍顶格（cf=5）**
   候选是「待人工确认」的草稿，满置信度偏高，可能让确认界面显得过于笃定。
   → M4.3 可把抽取默认 `confidence` 钳到 3–4，或让 prompt 输出更保守的置信度。

3. **content 带「用户」主语**
   如「用户叫小明，今年大三」。注入记忆块时可读，但略冗余（上下文已知是用户）。
   → M4.3 可让 prompt 省略主语，写「叫小明，今年大三」；同时保证关系类（relationship）仍明确主语以防歧义。

4. **（已修）pytest 在 `.env` 下仍 skip 的 harness bug**
   `HAS_KEY` 在 `load_dotenv()` 之前读取 `os.getenv` 导致 `python -m pytest` 永远 skip。
   已在 `tests/test_extraction_quality.py` 顶部显式 `load_dotenv()` 修复，现 5/5 全跑通。

## ⚠️ 安全提醒（重要）

你的**真实 API Key 当前写在 `.env.example`**（一个被 git 跟踪的文件）。一旦提交就会泄漏。
已为你从它复制出 `.env`（已被 `.gitignore` 忽略，安全）。**请尽快把 `.env.example` 的
`DEEPSEEK_API_KEY=` 改回占位符**（如 `sk-xxxx`），实际 key 只保留在 `.env`。本次未提交 `.env.example`。

## 下一步

据以上结果，M4 的「架构正确性 + 防污染」已验证通过。是否进入 M4.3 优化、以及优化哪几项，等你拍板。

## M4.3 优化已落地（2026-08-12 复测）

按你的选择「全部轻量优化」，已实施并复测通过（**4/4 PASS，Gate 仍 0 污染**）：

| 优化项 | 做法 | 复测效果 |
|--------|------|----------|
| 情绪→event 低权重 | 提示词区分「一次性 / 持续性」：持续性情绪/状态记 `event` 且低权重，绝不写成 `fact` | Case3「最近压力特别大」仍选择「不记录」（在规格允许的 event / 不记录二者内）；原则已写入，更强陈述会落 `event` 而非 `fact` |
| confidence 保守 | 提示词要求 3–4 **+ `parse_json_array` 代码封顶 4**（确定性，不依赖模型服从） | Case1 候选 `cf` 由 5 降到 4 |
| content 省主语 | 提示词：省略冗余「用户/用户」主语；`relationship` 保留明确主语防歧义 | Case1 `fact` 由「用户叫小明，今年大三」→「叫小明，今年大三」；`goal` 由「用户正在准备…」→「正在准备 AI Agent 方向实习」 |

复测后 Case1 实际候选：
- `[goal] imp=7 cf=4` 「正在准备 AI Agent 方向实习」
- `[fact] imp=6 cf=4` 「叫小明，今年大三」
- Gate: `long_term=0` ✅

### M4.3 质量观察
- **类型保真度提升**：身份与目标准确拆为 `fact` + `goal`。注意 M4.3 初版提示词曾让模型把二者合并成单条 `fact`（丢失 `goal` 类型），已通过强化「身份→fact / 目标→goal 严禁合并」修复——说明类型分离对「我的记忆」五分类 UI 很关键，断言必须守住。
- **Case3 未触发 event**：模型判断单句「最近压力特别大」信息量不足，选择不记。属规格允许的「不记录」分支；情绪→event 通道已就位，对更强/更持续的陈述会生效，且仍受「绝不为 fact」约束。
- **全程零污染**：Memory Gate 不变式稳定，无论怎么调提示词都不可能直写长期记忆正表。

## 最终下一步

M4 三阶段（接入 / 真实质量验证 / 轻量优化）已完成，架构正确性与防污染均验证通过。可进入后续阶段（多轮对话打磨 / UI 体验 / 候选批量操作等）。
