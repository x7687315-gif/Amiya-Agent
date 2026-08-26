# Amiya Agent

> 一个面向长期陪伴场景设计的、本地优先（local-first）的角色型 AI Agent 桌面应用。

Amiya Agent 的核心不是让一个 LLM「扮演一个角色」，而是探索如何把**稳定人格、角色知识、用户长期记忆、关系状态、情绪状态**与**模型生成能力**组织成一个可持续演进的 Agent 系统。

项目采用 **Persona / Knowledge / Memory 三分离** 的核心设计，同时通过 Agent 编排层将多个上下文来源以明确边界组装进 LLM Prompt。

当前项目是一个持续开发中的个人研究型 Agent 项目。公开仓库刻意**只提供通用代码、架构、测试、示例配置和中性占位资源**；私人记忆、个人资产、语音模型、API credentials 等均不包含在仓库中（详见 [SECURITY.md](SECURITY.md) 与 [开源边界](#27-privacy--open-source-boundary)）。

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Flet](https://img.shields.io/badge/UI-Flet%200.86.5-9cf.svg)
![DeepSeek](https://img.shields.io/badge/LLM-DeepSeek%20API-orange.svg)
![SQLite](https://img.shields.io/badge/Memory-SQLite-cyan.svg)
![RAG](https://img.shields.io/badge/Knowledge-RAG-green.svg)
![Memory](https://img.shields.io/badge/Agent-Memory%20Gate-success.svg)
![MIT License](https://img.shields.io/badge/License-MIT-yellow.svg)

> 说明：以上为静态技术栈徽章。本仓库暂未配置 GitHub Actions，因此**不放置 CI / coverage / release 徽章**（避免伪造状态）。

---

## 1. Project Overview

普通 ChatBot 的典型模式是把用户输入直接塞进 Prompt 交给 LLM：

```
User → Prompt → LLM → Response
```

而长期陪伴 Agent 需要把多个「上下文来源」组织起来：

```
                 ┌─────────────┐
                 │   Persona   │  我是谁？
                 └──────┬──────┘
                        │
                 ┌──────▼──────┐
                 │  Knowledge  │  我知道什么？
                 └──────┬──────┘
                        │
User ───────────────►  ┌──────▼──────┐
                        │    Agent    │  Orchestrator
                        └──────┬──────┘
                               │
       ┌───────────────────────┼────────────────────────┐
       ▼                       ▼                        ▼
   Memory                  Emotion                    LLM
   我经历过什么？          我现在是什么状态？          怎么生成回答？
```

本项目要解决的不是「一次回复写得好不好」，而是：

- 它应该**记得**与用户共同经历的事；
- 它应该有**稳定身份**，而不是在长上下文里逐渐漂移；
- 它应该区分「**它知道的事**」（角色设定）与「**它经历过的事**」（用户记忆）；
- 它应该**暴露自己的状态**（情绪、关系、记忆），但不假装自己是人类心智；
- 它的架构应该足够**可被理解**，使未来的版本仍能安全地被修改。

> **架构红线**：Agent = Decision & Orchestration。
> Agent 不是数据库、TTS、UI、播放器或工具执行器的实际实现地点。

---

## 2. Design Philosophy

### 2.1 Persona / Knowledge / Memory Separation

这是项目最重要的架构原则。三者分别回答不同的问题，且**不能互相污染**：

| 维度 | 回答 | 来源 |
|---|---|---|
| **Persona** | 我是谁？ | `config/persona/*.yaml`（identity / behavior / relationship / speech） |
| **Knowledge** | 我知道什么？ | 静态 Markdown corpus（`knowledge/`） |
| **Memory** | 我和用户经历过什么？ | SQLite memory system（`core/memory/`） |

- **Persona** 包含身份、视角/世界观、行为规则、语气风格、关系状态。
- **Knowledge** 来自静态 Markdown 知识语料，是「设定」。
- **Memory** 来自 SQLite 记忆系统，是「经历」。

> Persona ≠ Knowledge ≠ Memory。它们来源不同、生命周期不同、可信度与权限不同，必须物理与逻辑隔离。

### 2.2 Agent is an Orchestrator

Agent（`core/agent.py`）负责：

- 接收用户输入
- 确定当前上下文
- 请求 Memory Retrieval
- 请求 Knowledge Retrieval
- 构建 Prompt
- 调用 LLM（流式生成）
- 保存对话
- 发布阶段事件（`on_phase` / `on_retrieval`）
- 通知完成（`on_reply_complete`）
- 可选地触发记忆抽取钩子（默认关闭）

Agent **不负责**：SQL、SQLite schema、UI 渲染、TTS 引擎实现、音频播放、第三方模型内部逻辑。

### 2.3 Graceful Degradation

> 可选能力故障不应该摧毁聊天核心能力。

```
Memory failed      → chat continues
Knowledge failed   → chat continues
TTS failed         → text still works
Embedding missing  → hash fallback, still runnable
Extraction fails   → candidate discarded, conversation does not crash
```

辅助能力应该**增强** Agent，而不是成为 Agent 正常运行的单点故障。

### 2.4 Local-first, not local-LLM

根据当前实现与 [ROADMAP.md](ROADMAP.md)：

- **LLM**：DeepSeek API（云端推理）
- **Embedding**：本地 `bge-small-zh-v1.5`（CPU，可选；缺失时回退 stdlib 哈希）
- **Memory**：SQLite（本地持久化）
- **Knowledge**：Markdown（本地语料）
- **UI**：Flet（本地桌面）

不要把项目描述成「完全离线 Agent」。更准确地说：

> **Local-first application with cloud LLM inference and local persistent state.**

最小运行依赖见 [requirements.txt](requirements.txt)：Flet、requests、python-dotenv、PyYAML，以及测试用的 pytest。

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                         Flet UI                             │
│   Chat / Persona / Memory / Settings / Skin / Voice          │
└───────────────────────────┬─────────────────────────────────┘
                            │ callbacks / state
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                         Agent                               │
│                    orchestration layer                      │
│                                                             │
│ input → retrieval → prompt assembly → LLM → persist         │
│                 │                                           │
│                 ├──────── Memory  (MemoryManager)            │
│                 ├──────── Knowledge (KnowledgeManager)       │
│                 ├──────── Persona  (PersonaManager)          │
│                 └──────── Emotion  (detect_emotion)          │
└─────────────┬─────────────────────┬─────────────────────────┘
              │                     │
              ▼                     ▼
      ┌──────────────┐       ┌───────────────┐
      │ MemoryManager│       │KnowledgeManager│
      └──────┬───────┘       └──────┬────────┘
             │                      │
             ▼                      ▼
      ┌──────────────┐       ┌───────────────┐
      │ SQLite Store │       │ Markdown Corpus│
      └──────────────┘       └───────────────┘

                            │
                            ▼
                     ┌──────────────┐
                     │  LLMClient   │  (DeepSeek, OpenAI-compatible)
                     └──────────────┘

                            │
                            ▼
                     ┌──────────────┐
                     │ TTS Adapter  │  (external GPT-SoVITS process)
                     └──────────────┘
```

依赖方向：UI → Agent → 各领域管理器 → 存储/语料；UI 与领域管理器之间只通过事件/回调通信，UI 不理解 Memory/Knowledge 内部实现。

---

## 4. Agent Core

`core/agent.py` 的当前职责（来自其 docstring）：

> Persona loading → prompt building → streaming LLM → short-term history → MemoryManager persistence.

`Agent.__init__` 的可选能力（均为依赖注入，缺省即降级）：

| 参数 | 作用 |
|---|---|
| `persona` | 角色配置（Persona 对象） |
| `llm` | LLM 客户端（流式生成） |
| `history_limit` | 短期历史轮数上限 |
| `memory` | `MemoryManager`；为 `None` 时退化为纯内存、不落盘 |
| `knowledge` | `KnowledgeManager`；为 `None` 时不做知识检索 |
| `on_phase` | 阶段事件回调（`retrieving` / `reasoning` / `responding`） |
| `on_retrieval` | 检索命中回调（供记忆面板等 UI 消费） |
| `on_reply_complete` | 回复完成通知（含被停止后的部分回复） |
| `extraction engine` | 记忆抽取引擎（可选） |
| `extract_auto` | 是否每轮自动抽取候选（**默认 `False`**，钩子 dormant） |
| `memory_top_k` / `knowledge_top_k` | 每轮注入提示词的相关条数 |

> README 不是 API reference。重点在于：`Agent` 只做决策与编排，把「记忆/知识/人格/情绪」当作可替换的上下文来源组装进 Prompt。

---

## 5. Conversation Lifecycle

一轮回复的实际流程（依据 `core/agent.py` 与 [ROADMAP.md](ROADMAP.md)）：

```
User Input
    │
    ▼
Append Short-Term History
    │
    ▼
Persist User Turn
    │
    ▼
Memory Retrieval ──┐
    │               ├──── failure → degrade / continue
    ▼               ┘
Knowledge Retrieval ──┐
    │                  ├──── failure → degrade / continue
    ▼                  ┘
Emotion Detection (detect_emotion, local heuristic)
    │
    ▼
Relationship / Persona Runtime
    │
    ▼
PromptBuilder (identity → … → memory → relationship → emotion → behavior → speech → safety)
    │
    ▼
LLM Streaming
    │
    ▼
UI receives stream
    │
    ▼
Persist assistant response
    │
    ▼
Optional extraction hook (dormant unless extract_auto=True)
    │
    ▼
on_reply_complete  (notification only)
```

**重要边界**：`on_reply_complete` 只是**通知事件**，不负责 TTS。当前 `core/agent.py` 明确冻结了这个边界——语音播报由 UI / 独立的 TTS 层处理，Agent 核心不持有播放器。

---

## 6. Prompt Architecture

当前 `core/prompt_builder.py` 的注入顺序（固定，减少 Persona Drift）：

```
Identity
  → Perspective / Values
    → Knowledge
      → Memory
        → Relationship
          → Emotion
            → Behavior
              → Speech / Language
                → Safety / Shield
```

- **为什么人格在上下文之前？** 项目试图减少「长上下文 / 检索结果导致 Persona Drift」。
- **为什么 Knowledge 和 Memory 分开？** 因为 Knowledge = 静态角色/世界信息（设定），Memory = 用户特定经历（经验），两者来源、生命周期、可信度、权限都不同。
- 检索结果进入 Prompt 时使用**明确的定界符包裹**（见 §13），检索分数本身不注入模型上下文。

---

## 7. Memory Architecture

当前 Memory schema（`core/memory/store.py`，`SCHEMA_VERSION = 3`）包含六张核心表：

| 表 | 内容 |
|---|---|
| `conversation` | 短期对话轮次 |
| `memory` | 长期记忆正表（经闸门确认后写入） |
| `user_profile` | 用户侧画像（与记忆分表） |
| `persona_state` | **Agent 自身状态**（情绪、关系阶段、陪伴时长等），与用户记忆**严格分表** |
| `memory_blacklist` | 记忆控制黑名单（memory_control） |
| `memory_candidate` | 抽取候选队列（人工/显式确认后才转正） |

> 关系状态存于 `persona_state`，**不属于** `memory`——避免关系状态污染用户经历记忆。

---

## 8. Short-term vs Long-term Memory

```
Short-term history  ──►  当前 LLM context
Long-term Memory    ──►  SQLite ──► retrieval ──► relevant memory block
```

两者不是同一个东西：

- **短期**：为当前模型上下文服务（受 `HISTORY_LIMIT` 控制）。
- **长期**：为跨会话持续记忆服务（SQLite 持久化，可检索）。

关闭长期记忆（`MEMORY_ENABLED=0`）即退回纯内存模式，本地不留任何对话记录。

---

## 9. Memory Retrieval

当前检索使用**混合评分**（`core/memory/retrieval.py`）：

```
score = 0.40·vector + 0.20·keyword + 0.15·recency + 0.15·importance + 0.10·confidence
```

| 通道 | 权重 | 说明 |
|---|---|---|
| vector | 0.40 | 语义相似度（需 Embedding；缺失时该通道权重归零并重归一化） |
| keyword | 0.20 | 专名/关键词重叠（人名、项目名恰恰是陪伴场景的高信号） |
| recency | 0.15 | 新近性，让「最近的事」自然浮上来 |
| importance | 0.15 | 重要事实优先被想起（1..10 线性映射到 [0,1]） |
| confidence | 0.10 | 可信度（1..5 线性映射到 [0,1]） |

> 纯语义相似度不一定等于「值得记住」。长期陪伴场景中，新近性、重要性、置信度同样重要。

当向量通道缺失（无 embedding runtime / 网络 / 模型）时，权重会**重新归一化**降级，而不是整次检索抛异常。

---

## 10. Embedding Layer

`core/memory/embedder.py` 当前支持：

- 默认模型 `BAAI/bge-small-zh-v1.5`（约 33M 参数，CPU 推理毫秒级，不占显存）
- `device` 默认 `cpu`
- **backend 可替换**：`auto` / `local` / `hashing`
- **stdlib 哈希回退**：`hashlib.blake2b` 稳定哈希（`digest_size=8`）

```
Embedding interface
       │
       ├── Local BGE  (sentence-transformers，可选安装)
       └── Hash fallback (纯 stdlib，离线 / CI 可用)
```

> fallback 不是为了提供相同质量，而是为了保证在**没有 embedding runtime / 网络 / 模型**时，系统仍拥有可测试、可运行的 deterministic behavior。

说明：本地 BGE 后端依赖 `sentence-transformers`（不在最小 `requirements.txt` 中，需按需安装）；`EMBEDDING_BACKEND=auto` 在该依赖缺失时自动回退到哈希。

---

## 11. Memory Gate

这是项目的核心安全设计之一（`core/memory/extraction_engine.py` + `core/memory/store.py`）：

```
Conversation
     ▼
Extraction Engine   (LLM 提议候选)
     ▼
Candidate           (写入 memory_candidate 闸门，不直写正表)
     ▼
Human / explicit confirmation   (🧹 整理记忆 或 API confirm)
     ▼
memory             (转正后可被检索)
```

- 抽取引擎只把候选写入 `memory_candidate`，**绝不直写正表**（源码守卫）。
- `extract_auto` 默认 `False`：钩子存在但 **dormant**，手动整理优先。
- 真实 LLM 抽取 Provider 已通过 seam 接入（测试使用 FakeLLM），真实 DeepSeek 抽取（ROADMAP M4）仍待做。

> **LLM may propose memory; it does not unilaterally commit memory.**

---

## 12. Knowledge RAG

`core/knowledge/` 当前结构：

```
core/knowledge/
├── loader.py       # Markdown → 结构化 KnowledgeChunk
├── retriever.py    # 产生 KnowledgeHit（两通道：vector 0.7 + keyword 0.3）
└── manager.py      # loading / retrieval / rendering / graceful degradation
```

公开语料（`knowledge/`）为中性示例：

```
knowledge/
├── assistant_world.md
├── relationship_notes.md
├── speaking_style.md
└── values.md
```

- **Loader**：把 Markdown 切成结构化 chunk。
- **Retriever**：产生 `KnowledgeHit`，采用**两通道混合**（vector 0.7 + keyword 0.3），与 Memory 的五通道刻意不同（两者语料、生命周期、权限不同）。
- **Manager**：负责加载、检索、渲染，并在语料目录缺失时自动关闭知识检索（不报错）。

---

## 13. Knowledge / Memory Injection Boundary

检索结果进入 Prompt 时进行明确的定界符包裹：

```
【角色知识】
...
【相关用户记忆】
...
```

> 检索结果进入 Prompt 时进行明确的 delimiter wrapping；retrieval score 不直接注入模型上下文，避免将内部检索元数据暴露成 Prompt 内容，并减少不必要的提示词注入面。

这是 [ROADMAP.md](ROADMAP.md) 明确记录的设计原则，也是 Knowledge 与 Memory 物理隔离在 Prompt 层的延续。

---

## 14. Persona Engine

`core/persona/` 当前结构：

```
core/persona/
├── persona.py           # Persona 数据对象
├── loader.py            # 从 config/persona/*.yaml 加载
├── behavior_rules.py    # 行为规则
├── relationship.py      # 关系状态系统
└── persona_manager.py   # PersonaManager
```

兼容旧 import（兼容性门面，避免重构破坏旧调用方）：

```python
from core.persona import Persona
from core.persona import load_persona
from core.persona import PersonaManager
```

配置来自 `config/persona/`：`identity.yaml` / `behavior.yaml` / `relationship.yaml` / `speech.yaml`。公开仓库中的人格为**中性示例人格**，不绑定任何第三方 IP。

---

## 15. Relationship Engine

`core/persona/relationship.py` 维护关系状态（`trust` / `stage`），**独立于 Memory**（存于 `persona_state`）。

示例阶段（配置驱动，可在 `relationship.yaml` 中自定义）：

```
初识 → 熟悉 → 信任 → 深度陪伴
```

- `stage` 仅作为「背景事实」注入提示词（【与用户的关系】），**绝不控制语气**——语气由 `speech.yaml` 决定。
- 避免使用「家人」之类会改变产品语义的阶段。
- 关系状态描述的是关系状态，**不直接决定对话语气**。

> 当前 `stage` 为静态读取（默认 `trust=70`）；`set_trust` / `bump` 方法已就位但**自动涨跌未实现**（属未来阶段）。

---

## 16. Emotion System

`core/emotion.py` 当前实现为**轻量规则 / 关键词判定**：

- 本地、零额外 LLM 调用、可解释、低资源友好
- `detect_emotion(user_text)`：关键词表判定，优先级 `worried > happy > thinking > calm`
- `emotion_block(emotion)`：生成注入 Prompt 的情绪块
- `core/agent.py` **每轮自动调用** `detect_emotion` 并把情绪块注入 Prompt，同时把当前情绪持久化到 `persona_state.emotion`（系统状态表，与用户记忆分表）

```
Emotion ──► UI state / Skin mapping
Emotion ──► Prompt context
```

> 当前实现是**轻量启发式**，不是完整情绪模型。更丰富的情绪模型属于未来规划（见 Roadmap 🔵）。

重要边界：**Emotion 与 Skin 单向关联**——情绪可以决定皮肤，但 Skin 不反向影响 Agent 行为（见 §19）。

---

## 17. UI Architecture

依据 [UI_DESIGN.md](UI_DESIGN.md)：

核心产品理念：**从 Conversation 到 Presence**——让用户看到 Assistant 的 presence、state、trust、memory 与长期关系，而不只是聊天窗口。

```
Header
├── Persona / Status
├── Chat
└── Memory
```

设计契约（来自 UI 设计文档）：

- **desktop-first**，固定最小宽度 **960 × 640**
- 默认窗口 **1180 × 760**
- 三栏比例 **260（固定） : 1fr（弹性） : 300（固定）**
- **舰桥美学**是默认全站基调，不做独立主题开关（窄屏不降级，仅出横向滚动条）

---

## 18. Thinking / Phase Events

`on_phase` 当前阶段：

```
retrieving → reasoning → responding
```

UI 可根据阶段展示「Assistant 正在整理你的话……」以及检索状态。

> **UI 只消费 Agent 发出来的数据/事件，不负责理解 Memory/Knowledge 内部实现。**

---

## 19. Skin Architecture

`ui/design/skin.py` 的当前机制：

```
Skin ├── Colors
     ├── Avatar
     └── Background
```

Skin 是 **UI 层对象**。核心规则：

> **Skin does not control Agent behavior.**

```
Agent Emotion
   ▼
SkinManager.skin_for_emotion()
   ▼
Selected Skin
```

- `ui_skin = auto`：情绪 → 皮肤映射（`EMOTION_TO_SKIN`），情绪超过 **24h** 失效回落默认皮肤
- `ui_skin = <id>`：强制指定皮肤
- 未知 id / 映射缺失时回落默认皮肤（fallback）
- 启动期一次性应用主题（`theme.apply_skin`），运行期换肤重启生效
- 公开仓库当前仅含一套中性 `example` 皮肤；新增皮肤只需在 `resources/skins/<id>/` 下放置 `avatar.png` / `background.jpg` / `skin.json`

```
Emotion → SkinManager → Skin Profile   (单向)
不存在：Skin → LLM behavior
```

---

## 20. Voice Architecture

实际边界：

```
UI / Future Agent action
       ▼
TTS Interface
       ▼
External TTS backend  (GPT-SoVITS 独立进程)
       ▼
audio
       ▼
Player
```

> **Agent Core 不直接持有播放器，也不直接依赖 GPT-SoVITS 内部实现。**

当前公开仓库只提供 **interface / adapter / profile schema**（`core/tts/`），第三方模型权重不入库，模型路径通过环境变量 / 用户本地配置提供。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

---

## 21. Configuration

配置来自 `.env.example`（复制为 `.env` 后填写）。当前字段：

```
DEEPSEEK_API_KEY      # 你的 DeepSeek Key（必填，项目不附带）
DEEPSEEK_BASE_URL     # OpenAI 兼容接口地址
DEEPSEEK_MODEL        # 模型名
TEMPERATURE           # 生成温度
MAX_TOKENS            # 最大生成 token
TIMEOUT               # 请求超时
HISTORY_LIMIT         # 短期记忆轮数上限
MEMORY_ENABLED        # 是否启用 SQLite 长期记忆
MEMORY_DB_PATH        # 内存数据库路径
EMBEDDING_BACKEND     # auto / local / hashing
EMBEDDING_MODEL       # 嵌入模型名
EMBEDDING_DEVICE      # cpu / cuda
MEMORY_TOP_K          # 每轮注入记忆条数
KNOWLEDGE_DIR         # 知识语料目录
KNOWLEDGE_TOP_K       # 每轮注入知识条数
TTS_VOICE             # 语音档案名（对应 core/tts/voice_profiles/<name>.yaml）
TTS_TEXT_LANG         # 合成语言
TTS_ENABLED           # 语音总开关
UI_SKIN               # auto / 具体皮肤 id
```

> **`.env` never belongs in Git.**（已在 `.gitignore` 中忽略）

---

## 22. Failure Handling

| 故障 | 行为 |
|---|---|
| Memory unavailable | 对话继续（退化为纯内存） |
| Knowledge unavailable | 对话继续（不做知识检索） |
| Embedding backend unavailable | 回退哈希行为，系统仍可运行/测试 |
| TTS unavailable | 文本对话仍然可用 |
| Extraction fails | 候选被安全丢弃，对话不崩溃 |
| Storage errors | 设置 degraded 状态并尽可能继续 |

这是项目长期可用性的关键之一：**可选服务优雅降级，聊天核心不被拖垮**。

---

## 23. Testing Philosophy

> This project treats tests as **architecture guards** rather than only regression tests.

测试覆盖：

- unit / integration / UI / memory gate / persistence / fallback / configuration / launcher / skin tests

**测试基线（默认环境，未启动可选 GPT-SoVITS 服务）**：运行 `pytest` 得到 **276 passed, 9 skipped**。其中 9 个 skipped 包含 5 个 TTS 集成测试（`tests/test_tts_service.py` 的 `test_t1`–`test_t4` 与 `test_health_check_up`），它们需要一个在 `127.0.0.1:9880` 真实运行的 GPT-SoVITS API；该服务缺失时**自动跳过**（设计行为，避免把「服务没开」误判为代码回归）。

> 若环境里存在该服务但配置不正确，这些集成测试会真实发起合成并返回失败——属于环境依赖，不是代码缺陷。README 不永久硬编码测试数字；每次更新都会重新运行 `pytest` 确认基线。

---

## 24. Architecture Guardrails

已确定的架构红线：

1. **Agent = orchestration, not execution location.**
2. Core 不应该 import UI。
3. `TTSService` 不应该知道 Agent / UI。
4. Memory Core 不直接操作 Flet。
5. Skin 只属于 UI。
6. 真实用户数据不进入公开仓库。
7. Optional services degrade gracefully.
8. 不要为了未来可能发生的需求提前引入复杂框架。

---

## 25. 为什么现在不用 DI Framework / Microservices / Vector DB

当前**有意不做**以下事，原因在架构层面：

- **Dependency Injection Framework**：手工构造 + Composition Root 已足够；DI 框架增加认知与运行时成本，收益不大。
- **UI Framework Replacement**：问题在分层，不在 Flet；换 UI 框架不应改变领域层。
- **Microservices**：桌面单机应用没有必要；GPT-SoVITS 独立进程**只因为它自身包含 GPU/CUDA runtime**——这是 runtime boundary，不是把项目服务化。
- **Database migration framework**：SQLite 当前足够，手写 `SCHEMA_VERSION` 迁移可控。
- **sqlite-vec / 向量库**：当前 Memory 检索使用 brute-force cosine + 混合加权；若语料增长到需要，再引入向量索引——这是后续性能优化点，不是当前必要重构。

---

## 26. Development Roadmap

状态以当前 `master` 源码 + 测试为准（对照 [ROADMAP.md](ROADMAP.md)）。

### ✅ Implemented

- Core Agent（编排层）
- Persona Engine
- Long-Term Memory（SQLite）
- Memory Retrieval（五通道混合评分）
- Memory Gate（候选队列 + 确认/拒绝 API + 源码守卫；LLM 抽取 seam 已接入，默认 dormant）
- Knowledge RAG（loader / retriever / manager，两通道）
- Relationship State（存储 + 阶段注入；自动 trust 涨跌未实现）
- Flet Presence UI（三栏 + 舰桥基调）
- Skin System（auto / 指定 / 情绪映射 / 24h 过期 / fallback）
- Emotion detection（轻量关键词启发式，每轮自动调用并持久化）

### 🟡 Partial / In Progress

- Memory conflict resolution（冲突/置信度行为）
- Real BGE semantic recall verification（真实语义召回验证）
- sqlite-vec optimization（向量索引优化）
- UI polish
- Real LLM-based auto extraction（ROADMAP M4）+ Memory Gate UI panel（M3）

### 🔵 Future / Planned

- Tool orchestration
- TurnPipeline
- Proactive behavior（主动行为）
- ActionPlan
- Streaming TTS
- Richer emotion model

> 不要把未来写成已经实现。上述 ✅ 项均为当前代码已落地能力；🟡 为部分实现/进行中；🔵 为规划。

---

## 27. Privacy / Open Source Boundary

公开仓库包含：

- architecture / code
- tests
- examples（示例人格、知识、皮肤、配置 schema）
- 中性占位资源（程序生成的占位头像/壁纸）

**不公开**：

- API keys / tokens
- user memory（真实记忆数据库）
- real conversations（真实聊天记录）
- personal persona（私人人格设定）
- private skins（私人皮肤/壁纸）
- private audio（私人语音/录音）
- model weights（模型权重 / 语音模型）
- copyrighted assets without redistribution rights（无再分发权的版权素材）

详细安全策略见 [SECURITY.md](SECURITY.md)；第三方依赖与许可证见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；许可证见 [LICENSE](LICENSE)。

---

## 28. Third-party Dependencies

| Dependency | Purpose |
|---|---|
| Flet | Desktop UI |
| requests | HTTP / LLM / 外部服务通信 |
| python-dotenv | 环境变量配置 |
| PyYAML | YAML 配置 / Persona 加载 |
| pytest | 测试 |

Embedding 本地 BGE 后端可选依赖 `sentence-transformers`（不在最小 `requirements.txt`）。
GPT-SoVITS 与 DeepSeek 为**外部服务**，不随仓库再分发——前者经环境变量指向本地进程，后者经 API 调用。

> 依赖许可证与「外部服务不随仓库分发」的声明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。当前依赖版本见 [requirements.txt](requirements.txt)。

---

## 29. Quick Start

```bash
# 1. 虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置
cp .env.example .env
#   编辑 .env，填入：DEEPSEEK_API_KEY=your_api_key_here

# 4. 启动
python main.py
```

> **DeepSeek API Key 必须由用户自行提供；项目不附带模型 key。**

语音（🔊 朗读）为可选能力：需用户自备 GPT-SoVITS 运行时并通过环境变量/本地配置指定，仓库不含任何语音模型权重。

---

## 30. Configuration Reference

| Variable | Meaning | Default |
|---|---|---|
| `DEEPSEEK_MODEL` | LLM 模型 | `deepseek-chat` |
| `TEMPERATURE` | 生成温度 | `0.9` |
| `MAX_TOKENS` | 最大生成 token | `1024` |
| `TIMEOUT` | 请求超时（秒） | `30` |
| `HISTORY_LIMIT` | 短期对话轮数 | `20` |
| `MEMORY_ENABLED` | 启用 SQLite 记忆 | `1` |
| `MEMORY_DB_PATH` | 记忆数据库路径 | `data/assistant.db` |
| `EMBEDDING_BACKEND` | 嵌入后端 | `auto` |
| `EMBEDDING_MODEL` | 嵌入模型 | `BAAI/bge-small-zh-v1.5` |
| `EMBEDDING_DEVICE` | 嵌入设备 | `cpu` |
| `MEMORY_TOP_K` | 记忆检索条数 | `5` |
| `KNOWLEDGE_DIR` | 知识语料目录 | `knowledge` |
| `KNOWLEDGE_TOP_K` | 知识检索条数 | `4` |
| `TTS_VOICE` | 语音档案名 | `assistant` |
| `TTS_TEXT_LANG` | 合成语言 | `zh` |
| `TTS_ENABLED` | 语音总开关 | `1` |
| `UI_SKIN` | 皮肤 / auto | `auto` |

具体值必须与当前 `.env.example` 保持同步。

---

## 31. Example Extension

项目为「可替换、可扩展」而设计：

- **新增 Persona**：编辑 / 新增 `config/persona/*.yaml`
- **新增 Knowledge corpus**：新增 `knowledge/*.md`
- **新增 Skin**：在 `resources/skins/<skin-id>/` 放置 `avatar.png` / `background.jpg` / `skin.json`
- **新增 LLM provider**：实现 LLM abstraction（`core/llm_client.py` 风格），**不要修改 Agent 核心**
- **新增 TTS backend**：实现 Voice adapter，**不要让 Agent 直接 import 后端内部**

> Architecture is designed for replacement and extension.

---

## 32. Project Maturity

> This is a personal research/development project.

The architecture is intentionally evolving; some modules are stable foundations while others remain experimental or planned. The repository is most useful as **an architectural reference and an experimental long-term companion Agent implementation** — 而不是「生产级产品」。

---

## 33. Philosophy

Amiya Agent is built around a simple idea:

> A long-term companion should not be defined only by the quality of a single response.

It should **remember**.
It should have a **stable identity**.
It should distinguish what it **knows** from what it **experienced**.
It should expose its **state** without pretending to be a human mind.
And its architecture should remain **understandable** enough that a future version can still be changed safely.

The project therefore treats Persona, Knowledge, Memory, Emotion and Agent orchestration as separate systems that evolve together without collapsing into one monolithic component.

Made as a personal exploration into long-term AI companionship, memory architecture, and agent engineering.

---

### 相关文档

- [ROADMAP.md](ROADMAP.md) — 架构冻结文档 + 实际落地进度 + 未来方案
- [UI_DESIGN.md](UI_DESIGN.md) — UI 设计契约（舰桥美学、三栏布局）
- [SECURITY.md](SECURITY.md) — 安全与开源边界策略
- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) — 第三方依赖与许可证
- [LICENSE](LICENSE) — MIT License
- [docs/](docs/) — 更底层的架构 / 记忆数据流 / Persona / 抽取设计文档（实现细节留在 docs，不在 README 重复）
