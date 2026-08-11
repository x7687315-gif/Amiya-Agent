# 01 · assistant-agent 当前真实架构（主线）

> 写于 2026-08-05。本文档描述 **`<ASSISTANT_AGENT_DIR>` 主线** 的真实架构。
> 它是四份文档里最权威的一份——`C:\Assistant` 下的 01~04 仅描述那个 MVP 仓库，
> 而主线（本仓库）才是目标架构的实装。详见 `C:\Assistant\docs\00_REPO_LANDSCAPE.md`。

---

## 1. 一句话结论

**assistant-agent 已经是一套分层、可测试、带人工记忆闸门的工程化架构，并非需要重写的草稿。**
它相对「目标架构」的差距主要在 *数据*（从未积累过真实对话）和 *Step 2.7 抽取器未接入*，
而不是在 *设计*。本仓库最该做的是「灌入真实数据 + 把已建好的闸门接到抽取器」，
而不是推倒重来。

---

## 2. 分层架构

严格的三层 + 两个独立支柱（Knowledge / Emotion seam）：

```
┌──────────────────────────────────────────────────────────────────┐
│  UI 层  ui/app.py · ui/components/*   (Flet 三栏，只读 MemoryManager) │
└───────────────────────────────┬──────────────────────────────────┘
                                  │ 仅依赖 MemoryManager 协议
┌───────────────────────────────┴──────────────────────────────────┐
│  Agent 编排层  core/agent.py                                     │
│  · reply() 流式；先落对话 → 检索 → 拼系统提示 → 调 LLM             │
│  · 只认识 MemoryManager / KnowledgeManager 协议，不碰 SQL          │
└───────┬───────────────────────────────────┬──────────────────────┘
        │                                    │
┌───────▼────────────┐            ┌──────────▼───────────┐
│ MemoryManager      │            │ KnowledgeManager     │  ← 独立支柱，不混记忆
│ (core/memory/)     │            │ (core/knowledge/)    │
│ 闸门 / 检索 / 治理  │            │ 角色设定 RAG         │
└───────┬────────────┘            └──────────┬───────────┘
        │                                    │
┌───────▼────────────┐            ┌──────────▼───────────┐
│ SQLiteMemoryStore  │            │ KnowledgeCorpus/     │
│ (core/memory/      │            │ Retriever            │
│  store.py, schema  │            │ (Markdown 切块)       │
│  v3)               │            └──────────────────────┘
└────────────────────┘
```

三柱定界（`core/prompt_builder.py`）：身份锚定 → 【角色知识】 → 【相关用户记忆】 → 【当前情绪·暂留 seam】。
**知识是「设定」、记忆是「经历」、情绪是「当下」——三者用独立定界块包裹，分数绝不进提示词。**

---

## 3. 目录与模块职责

| 路径 | 职责 | 关键事实 |
|------|------|---------|
| `main.py` | 入口 | `ft.run(ui.app.main)` |
| `config.py` | 配置加载（`.env` → `Settings`） | `MEMORY_ENABLED` 默认 True；Knowledge 无开关（目录缺失即自动关闭） |
| `core/agent.py` | 编排层 | `reply()` 流式；`_remember` 只落对话；`retrieve()` 真实检索；`build_system(memory_block, knowledge_block=)` |
| `core/persona.py` | 人格加载 | 从 `config/persona/*.yaml` 读身份/语言风格，与代码解耦 |
| `core/llm_client.py` | LLM 客户端 | `DeepSeekLLMClient.stream_chat` |
| `core/prompt_builder.py` | 系统提示拼装 | 三柱定界；`emotion_block` 参数为预留 seam |
| `core/memory/store.py` | 存储层 | `SQLiteMemoryStore`，schema v3；协议 `MemoryStore`；WAL + RLock |
| `core/memory/manager.py` | 记忆中间层 | `add_turn / remember / propose / confirm_candidate / retrieve / reindex / forget …` |
| `core/memory/embedder.py` | 嵌入 | `bge-small-zh-v1.5`；纯 stdlib 哈希回退；`get_embedder(auto/local/hashing)` |
| `core/memory/retrieval.py` | 检索 | 五通道混合打分（vector/keyword/recency/importance/confidence） |
| `core/knowledge/` | 角色知识 RAG | `KnowledgeManager` + `loader` + `retriever`；与记忆**彻底隔离** |
| `ui/app.py` | Flet 三栏应用 | 装配 store/embedder/memory/knowledge/agent；失败全部降级而非致命 |
| `ui/components/` | 组件 | `memory_panel`（右栏，走人工确认闸门）、`chat_area`、`persona_status` 等 |
| `tools/` | 开发工具 | `manual_memory_test.py`、`memory_inspector.py` |
| `tests/` | 测试 | **105 passed**（2026-08-05 基线） |

### SQLite schema（v3，`core/memory/store.py`）

| 表 | 作用 | 是否记忆 |
|----|------|---------|
| `conversation` | 原始对话流水 | ❌ 不是记忆（对话日志） |
| `memory` | 长期记忆正表（type: fact/preference/event/goal/relationship） | ✅ 记忆 |
| `memory_candidate` | 人工确认队列（pending/confirmed/rejected） | 🔒 闸门 |
| `user_profile` | 用户画像（key-value） | ⚠️ 另表，非 memory |
| `persona_state` | Agent 自身状态（trust/companionship/emotion/total_turns） | ❌ Agent 状态，非用户记忆 |
| `memory_blacklist` | 记忆黑名单（memory_control） | 🔒 控制面 |

> 注意 `memory.decay_rate` / `last_confirmed_at` 字段已预留但**暂不参与逻辑**（Phase 2-C 后启用）。

---

## 4. 启动与依赖注入（`ui/app.py`）

```
load_settings() → load_persona()
  ├─ MEMORY_ENABLED? → SQLiteMemoryStore + get_embedder → MemoryManager(reindex 补向量)
  │                   失败 → memory = None（退回纯内存，不致命）
  ├─ build_knowledge_manager(knowledge_dir) → 目录缺失/失败 → knowledge = None（不致命）
  └─ Agent(persona, llm, memory=, knowledge=, memory_top_k=, knowledge_top_k=)
```

- 记忆/知识任一初始化失败都**只降级、不打断启动**——可用性优先。
- 冷启动 `MemoryManager.reindex()`：空库直接返回 0，不会触发模型加载。

---

## 5. 配置与开关（`config.py`）

| 开关 | 默认 | 含义 |
|------|------|------|
| `MEMORY_ENABLED` | `True` | 关闭后 Agent 退回 Phase 1 纯内存行为 |
| `EMBEDDING_BACKEND` | `auto` | auto / local / hashing（无模型时自动回退哈希） |
| `EMBEDDING_MODEL` | `BAAI/bge-small-zh-v1.5` | 向量模型；换模型后旧向量由 `reindex()` 重算 |
| `MEMORY_TOP_K` | `5` | 每轮注入提示词的最相关记忆条数 |
| `KNOWLEDGE_TOP_K` | `4` | 每轮注入的最相关知识片段条数 |
| `KNOWLEDGE_DIR` | `knowledge` | 角色知识语料目录；缺失则知识检索静默关闭 |

> **Emotion 没有任何开关**——它不是「关着的模块」，而是 `PromptBuilder.build_system(emotion_block=)` 的预留参数，当前永远传 `None`。
> **Knowledge 没有独立的 enable 开关**（仅由目录存在性隐式控制）；如需严格「暂不启用」，建议加 `KNOWLEDGE_ENABLED`（见 03-G3 / 04-M4）。

---

## 6. 测试基线

```
.venv/Scripts/python.exe -m pytest -q  →  105 passed in 3.13s   (2026-08-05)
```

覆盖：store 迁移链、记忆闸门（propose→confirm→reject）、检索五通道、手动 remember/forget、
编辑、黑名单、prompt 注入、UI 组件、知识检索、配置加载等。

---

## 7. 与 `C:\Assistant`（MVP 仓库）的本质差异

| 维度 | `C:\Assistant`（MVP，已冻结） | `<ASSISTANT_AGENT_DIR>`（主线，本文档） |
|------|--------------------------|----------------------------------|
| 记忆写入 | **曾有 LLM 直写画像（无闸门，已关闭）** | **全程无 LLM 直写记忆** |
| 记忆闸门 | 本次才建，且下游断链 | **Step 2.3 早已闭环**（表 + UI 确认/否决） |
| 检索 | 无（固定取末 3 条） | 五通道混合 + 向量 |
| 存储 | JSON ×5，无 schema 版本 | SQLite v3，增量迁移链 |
| 真实数据 | 121 条对话（有温度） | **0 条（从未运行）** |
| 测试 | 49 passed | 105 passed |

> 一句话：**主线设计正确、缺的是数据与真实使用；MVP 有数据、缺的是架构。**
> 迁移方向是把 MVP 的真实数据灌进主线的正确架构，而不是反过来。

---

## 8. 本文档结论对「第一优先级」的回答

用户要求的第一优先级是：「修复 memory 自动写入问题，引入 candidate → confirm → long term 的记忆闸门。」

**在主线 assistant-agent 中，这条已经满足：**
- 没有任何代码路径会把 LLM 输出直接写进 `memory` 表；
- 唯一的自动写入是 `conversation` 流水（对话日志，不是记忆，且有 `role` 校验、空白跳过）；
- 每条记忆要么由用户显式触发（`remember`，等价于「已确认」），要么先经 `propose` 落候选、
  再经 UI「记住 / 不用记」或 `confirm_candidate` 转正——**闸门完整闭环**。

因此主线当前真正要做的是 04 文档里的 **M1（灌入真实数据）** 与 **M3（把 2.7 抽取器接到闸门）**，
而非「修复一个并不存在的自动写入 bug」。
