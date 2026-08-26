# Step 2.7 LLM 自动抽取 — 架构设计文档

> 状态：设计稿（待确认，未实现）。本文件只描述架构，不写实现代码。
> 前置基线：`99da80f`（Step A Memory Gate）+ `70a6b77`（验证 harness）+ `eb62625`（Phase 3 Persona）。
> 目标：对话中由 DeepSeek 自动抽取**候选记忆**，但**绝不直写长期记忆正表**，一律走人工确认闸门。

---

## 0. 一句话结论

```
message
   ↓
Agent.reply() 落盘 conversation（短期 + 旁系长期）
   ↓（仅当 EXTRACT_AUTO=1 或手动「🧹 整理记忆」）
ExtractionEngine.extract(window)        ← 用【独立】抽取提示词调 DeepSeek（非助手人格）
   ↓
parse JSON → 校验 → ExtractedMemory[]
   ↓
MemoryExtractor.propose_many([...])     ← 唯一写路径
   ↓
memory_candidate（status=pending）       ← 闸门：绝不进 memory 正表
   ↓
人确认 → confirm_candidate → memory（转正，可检索）
人否决 → reject_candidate  → 丢弃（同内容不再打扰）
```

三柱隔离结论：**DeepSeek 抽取器只触达 `memory_candidate`（Memory 柱）；Knowledge 柱（`knowledge/*.md`）、Persona 柱（`config/persona/*` + `persona_state`）全程只读 / 不被抽取器触碰。抽取 LLM 用独立 system prompt，绝不注入助手人格。**

---

## 1. 数据结构

### 1.1 已存在（Step A，保持不变）

```python
# core/memory/extractor.py — 已落地
@dataclass(frozen=True)
class ExtractedMemory:
    type: str            # fact | preference | event | goal | relationship
    content: str         # 原子化、稳定的事实陈述
    importance: int = 3  # 1–10，越高越优先转正与召回
    confidence: int = 3  # 1–5，抽取器对"它是否真的稳定"的把握
    reason: str | None = None        # 抽取依据（给人确认时看）
    source_msg_id: int | None = None # 溯源：来自哪条 conversation
    # __post_init__ 校验 type ∈ MEMORY_TYPES，非法抛 ValueError
```

`memory_candidate` 表（store.py schema v2，已存在）：

| 列 | 含义 |
|----|------|
| `id` | 候选主键 |
| `type` / `content` | 同 ExtractedMemory（`UNIQUE(type, content)` 防重） |
| `confidence` / `importance` | 权重 |
| `reason` | 抽取依据 |
| `source_msg_id` | 溯源 |
| `status` | `pending` / `confirmed` / `rejected` |
| `created_at` / `decided_at` | 时间锚 |
| `memory_id` | 转正后回指 memory 正表 id |

### 1.2 本步新增

**(a) `LLMClient.chat()` — 非流式、返回完整文本（抽 JSON 用）**

当前 `LLMClient` 协议只有 `stream_chat`。抽取器需要一次性拿回完整 JSON，必须补一个非流式方法。

```python
# core/llm_client.py — 协议与方法新增（设计稿）
@runtime_checkable
class LLMClient(Protocol):
    def stream_chat(self, system: str, history) -> Iterator[str]: ...
    def chat(self, system: str, history, *, temperature=None, max_tokens=None) -> str:
        """非流式：返回模型完整回复文本（用于结构化 JSON 抽取）。"""
        ...

# DeepSeekLLMClient.chat：同一 /chat/completions，stream=False，聚合 choices[0].message.content
```

**(b) `EXTRACT_AUTO` 配置项（config.py）**

```python
# config.py — Settings 新增字段（设计稿）
extract_auto: bool          # 默认 False（闸门绝不绕过；默认关）
extract_window: int = 20    # 抽取看的最近轮数（*2 条）
extract_max_candidates: int = 5   # 单次最多入队候选数（防爆队列）
extract_min_interval_turns: int = 3  # 自动模式下多少轮才抽一次（节流）
# load_settings() 中：extract_auto=_env_bool("EXTRACT_AUTO", False)
```

**(c) `ExtractionEngine`（新文件 `core/memory/extraction_engine.py`）**

```python
class ExtractionEngine:
    """把"近 N 轮对话"变成候选记忆并送进闸门。

    不持有直写正表能力——内部只调用 MemoryExtractor.propose_many，
    物理上仍只能落 memory_candidate。
    """
    def __init__(self, extractor: MemoryExtractor, llm: LLMClient) -> None:
        self._extractor = extractor
        self._llm = llm

    def extract(self, window: List[Dict[str, str]], *,
                existing_summary: str | None = None) -> List[int]:
        """调 DeepSeek → 解析 JSON → 校验 → propose_many。返回候选 id 列表。
        解析失败 / LLM 异常 → 返回空列表（静默降级，绝不崩对话）。"""
```

> 设计选择：新增 `ExtractionEngine` 而非在 `MemoryExtractor` 上塞 LLM 逻辑，是为了**保住 Step A 的"写-only seam"不变**——`MemoryExtractor` 依旧只认识 `propose`，LLM+解析是另一层。依赖方向：`ExtractionEngine → MemoryExtractor → MemoryManager.propose → store.memory_candidate`，严格单向向下。

**(d) `last_extract_msg_id` 记账（已建字段，本步接通）**

`persona_state.last_extract_msg_id` 已在 schema 中。本步经 `MemoryManager.state()/save_state()` 读写，保证同一窗口只抽一次（防循环重抽）。

---

## 2. 调用链

```
用户消息
  │
  ▼
Agent.reply(user_text)
  ├─ _history.append(user)
  ├─ _remember("user", ...)            # 落盘 conversation（短期窗口 + 旁系长期）
  ├─ retrieve(knowledge / memory)      # 注入系统提示词（Knowledge + Memory 柱）
  ├─ stream_chat(ASSISTANT_SYSTEM_PROMPT)  # 助手本体回复（Persona 柱提示词）
  └─ _remember("assistant", full)
        │
        ▼  if extract_auto or 手动触发:
        _maybe_extract(window)
            │
            ▼
        ExtractionEngine.extract(window)
            ├─ llm.chat(EXTRACTION_SYSTEM_PROMPT, window)   # ★独立提示词，非助手
            ├─ parse_json_array(safe)  → [raw]
            ├─ 逐条 validate(type/len/blacklist) → ExtractedMemory[]
            └─ extractor.propose_many(candidates)          # ★唯一写路径
                    │
                    ▼
            store.memory_candidate (status='pending')
                    │
        ┌───────────┴─────────────┐
        ▼（人点「记住」）          ▼（人点「不用记」）
 confirm_candidate              reject_candidate
        │                            │
        ▼                            ▼
 memory 正表（可被检索）        status='rejected'（永不进正表）
 + 立即补向量                    + 同内容不再被打扰
```

**关键不变量（设计契约）**
1. 抽取 LLM 与回复 LLM 是**两次独立调用、两条独立 system prompt** → 助手人格不泄漏进抽取器（Persona 柱隔离）。
2. 抽取器**只写 `memory_candidate`**，永远不经过 `upsert_memory`/`remember` → 长期记忆正表只有"人确认"这一条入口（Memory 柱内部隔离）。
3. `retrieve` 只读 `memory` 表（`retrieval_rows` 的 SELECT 来源是 `memory`），候选在确认前**结构上不可能**出现在检索结果里 → 未确认记忆零污染召回。

---

## 3. 三大模块（每个模块：设计目的 / 数据流 / 核心代码解释）

### 模块 A — MemoryExtractor（LLM 抽取器 / 抽取引擎）

**① 设计目的**

> 为什么需要 MemoryExtractor？

因为**聊天记录 ≠ 长期记忆**。对话是连续的、含噪声的、夹带临时情绪的；长期记忆只该保留"稳定、可复用、对用户有用"的事实。直接从对话里 `memory.save(message)` 会把"今天天气不错""刚才卡了个 bug"这类瞬时噪声固化成"用户喜欢晴天""用户不会写代码"——这是长期陪伴 Agent 最致命的污染。抽取器的存在，就是把"噪声的对话流"过滤成"原子化、可确认的稳定事实候选"。

**② 数据流**

```
近 N 轮对话 window
   ↓
llm.chat(抽取提示词)
   ↓
JSON 数组
   ↓
validate（type / 长度 / 黑名单）
   ↓
ExtractedMemory[]
   ↓
propose_many
   ↓
memory_candidate（pending）
```

**③ 核心代码解释**

```python
candidates = engine.extract(window)        # 调 DeepSeek，解析，落闸门
# 或等价地：
cands = [ExtractedMemory("preference", "用户偏好先写测试再写实现", importance=7, confidence=4)]
ids = extractor.propose_many(cands)        # 唯一写路径
```

> 为什么不是 `memory.save(window)` / `upsert_memory(type, content)`？
> - `upsert_memory` 是**直写正表**——跳过人工确认，候选在未审核时就进了可被检索的长期记忆，污染召回。
> - `memory.save(window)` 粒度是"整段对话"，既非原子事实、又无类型/权重，且同样绕过闸门。
> - `propose_many` 只写 `memory_candidate(pending)`，把"是否进长期记忆"的决定权交还给人。DeepSeek 只负责**提出**，不负责**写入**。

---

### 模块 B — Candidate Memory（memory_candidate 中间层）

**① 设计目的**

> 为什么需要 Candidate Memory 这一中间层？

因为"记错"比"忘记"更伤。任何自动产出的记忆（LLM 抽取、工具总结）在转正前都**不可信**。中间层给每一条候选一个"待审"状态（`pending`），配溯源（`source_msg_id`）、权重（`importance/confidence`）、依据（`reason`），让人能在确认前看到"AI 想记什么、为什么"。它同时是去重与黑名单的执行点——同一事实反复提到只加权不刷屏，被否决的内容永不复发。

**② 数据流**

```
propose(type, content, ...)
   ↓
is_blacklisted?  ──是──► 返回 0（拦截）
   ↓否
UNIQUE(type, content)?
   ├─ 已存在 pending ─► 更新权重（同事实加权，不重复打扰）
   ├─ 已存在 confirmed/rejected ─► 返回 0（不重复打扰）
   └─ 全新 ─► INSERT status='pending' → 返回 id
```

**③ 核心代码解释**

```python
cand_id = memory.propose("preference", "用户偏好先写测试再写实现")
# 落点：memory_candidate(status='pending')，绝不碰 memory 正表
```

> 为什么不是"直接写 memory 表 + 标个 is_candidate 字段"？
> - 混在同一张表要靠 `WHERE status='confirmed'` 处处过滤，检索/统计处处埋雷，且极易漏过滤导致候选泄漏进召回。
> - 分表（`memory` vs `memory_candidate`）让 `retrieve` 的物理来源就是 `memory` 一张表，候选**结构上不可能**被召回——隔离由存储保证，而非靠代码纪律。
> - 这也正是 Step 2.3 已落地的设计：`retrieval_rows` 只 SELECT `memory`。

---

### 模块 C — Gate（人工确认 / 否决闸门）

**① 设计目的**

> 为什么需要 Gate（confirm / reject）？

因为"是否成为长期记忆"是**人的主权**，不是 AI 的。闸门把"AI 建议"与"用户事实"明确分层：AI 可以提议，但只有用户点头，候选才转正进 `memory`；用户摇头，候选被标记为 `rejected` 并永久不再打扰。这让助手在"主动想记住"和"不乱记"之间取得平衡——既不会失忆，也不会臆造。

**② 数据流**

```
memory_candidate(pending)
   ├─ 人点「记住」→ confirm_candidate
   │       ├─ 写 memory 正表（同 type+content 则更新权重）
   │       ├─ status='confirmed'，memory_id 回指
   │       └─ 立即补向量（转正即可检索）
   └─ 人点「不用记」→ reject_candidate
           └─ status='rejected'（同内容不再被 propose 唤醒）
```

**③ 核心代码解释**

```python
mem_id = memory.confirm_candidate(cand_id)   # 人确认 → 转正
# 或
memory.reject_candidate(cand_id)             # 人否决 → 丢弃
```

> 为什么不是"AI 自判置信度够了就自动转正"？
> - 自动转正把模块 A 的污染风险直接放行进长期记忆，闸门形同虚设。
> - 即便 `confidence=5`，也可能是 DeepSeek 的幻觉（把玩笑当真）。只有"人确认"这一动作能终结候选生命周期。
> - 代价是每次抽取都需一次人工决策；用**默认关 + 节流 + 手动「🧹 整理记忆」按钮**把成本压到可接受。

---

## 4. Prompt 设计

### 4.1 抽取提示词（EXTRACTION_SYSTEM_PROMPT）

```
你是「助手的记忆整理助手」。你的唯一任务是：从最近一段对话里，
抽取出【值得长期记住的、稳定的】事实。你不是助手本人，不要代入其
语气，也不要和用户对话——只输出结构化结果。

抽取原则：
1. 只抽"稳定且可复用"的事实：长期偏好、反复出现的习惯、重要目标、
   明确的身份/关系事实、已确定的计划。
2. 不要抽瞬时信息：今天天气、刚才某句玩笑、一次性吐槽、情绪波动。
   例如"今天天气不错"→ 不抽；"我一般周末去爬山"→ 抽(preference)。
3. 一条候选只承载一个事实，content 用中性陈述句，忠于用户原意，不臆造。
4. 不要与已有记忆重复：若明显已记住，不要重复产出。
5. 不输出任何解释、不闲聊、不用 markdown 代码块包裹。

输出格式（纯 JSON 数组，无其它内容）：
[
  {"type": "preference|fact|event|goal|relationship",
   "content": "稳定的事实陈述",
   "importance": 1-10,
   "confidence": 1-5,
   "reason": "为什么值得记（给人看）"}
]
无候选时输出 []。
```

要点（对应隔离与风险）：
- **独立身份**：明确"你不是助手本人"，杜绝人格泄漏（Persona 柱隔离）。
- **稳定优先**：用硬规则挡住瞬时噪声（风险 R1）。
- **纯 JSON**：便于 `parse_json_array` 稳健解析（风险 R2）。
- `importance/confidence` 让人在确认时一眼判断价值，且低默认分使幻觉危害可控。

### 4.2 抽取输入（history / window）

- 来源：`Agent._history` 最近 `extract_window` 轮（user + assistant）。
- 可选 `existing_summary`：当前 pending + 近期 confirmed 记忆摘要，喂给模型以减少重复（去重第二道防线；第一道是 `UNIQUE(type,content)`）。
- **不**把助手 persona 系统提示词混入抽取输入。

### 4.3 解析与校验（parse_json_array）

```python
def parse_json_array(text: str) -> List[ExtractedMemory]:
    # 1. 去 ```json 围栏 / 前后废话，定位首个 [ 与末个 ]
    # 2. json.loads；失败 → 返回 []
    # 3. 逐条 map：type 合法？content 非空且 ≤ 上限(如 200 字)？
    #    非法整条丢弃；合法 → ExtractedMemory(...)
    # 4. 截断到 extract_max_candidates 条
```

---

## 5. 风险点

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | DeepSeek 把瞬时当稳定（"今天天气不错"→"喜欢晴天"） | 记忆污染 | 抽取提示词硬规则 + 低默认 confidence + 人工闸门 + 黑名单 |
| R2 | JSON 解析失败 / 模型输出非 JSON | 抽取静默失效 | `parse_json_array` 宽容解析 + 整体 try/except 静默跳过，**绝不崩对话** |
| R3 | 同窗口反复抽取 → 候选刷屏 | 打扰用户 | `last_extract_msg_id` 记账（同窗口只抽一次）+ `UNIQUE(type,content)` 加权不重复 |
| R4 | 抽取 LLM 调用阻塞流式回复 | 延迟/卡顿 | 自动模式走**后台线程/队列**；默认关；手动「🧹 整理记忆」显式触发 |
| R5 | 对话上送 DeepSeek 云的隐私 | 数据外发 | 与聊天同源（已是 DeepSeek）；**默认关**；可后续支持本地模型抽 |
| R6 | 候选队列过载（每次都产一堆） | 确认疲劳 | `extract_max_candidates` 上限 + `extract_min_interval_turns` 节流 + 默认关 |
| R7 | 未确认候选泄漏进 retrieve | 污染召回 | 结构隔离：`retrieval_rows` 只读 `memory` 表（已验证） |
| R8 | 非法类型 / 超长 content | 入库异常 | `ExtractedMemory.__post_init__` 校验 type；解析层长度截断；`propose` 黑名单 + 空串拦截 |
| R9 | 抽取 LLM 意外写入 Knowledge/Persona | 三柱串味 | `ExtractionEngine` 只依赖 `MemoryExtractor`（→ `memory_candidate`）；源码守卫测试断言不 import knowledge/persona 写路径 |

---

## 6. 测试方案

> 约定：用仓库 `.venv`（`<ASSISTANT_AGENT_DIR>\.venv\Scripts\python -m pytest`）跑；抽取 LLM 用 `FakeLLM`（实现 `LLMClient.chat` 返回固定 JSON），不联网。

| 测试 | 验证点 | 手段 |
|------|--------|------|
| T1 抽取入队 | `FakeLLM` 返回 JSON → `extract()` 产出候选 → `pending_candidates` 可见 | 单元 |
| T2 转正可检索 | `confirm_candidate` → `memory` 正表 + `MemoryManager.retrieve` 命中 | 单元（Gate） |
| T3 reject 不污染 | `reject_candidate` → 不进 memory、`retrieve` 读不到、同内容不再被 propose | 单元（Gate） |
| T4 解析失败降级 | `FakeLLM` 返回乱码 → `extract()` 返回 `[]`，对话不中断 | 单元 |
| T5 临时信息不抽 | 输入"今天天气不错"风格 → `FakeLLM` 返回 `[]` → 无候选 | 单元（prompt 规则/解析） |
| T6 三柱隔离 | 跑完整 `extract()` 后：`knowledge/` 目录未变、`persona_state` 除 `last_extract_msg_id` 外未变、无 knowledge/persona 写调用 | 集成 + 源码守卫 |
| T7 默认关 | `Agent(extract_auto=False)` 跑 `reply()` → 抽取器 spy 不被调用 | 集成 |
| T8 去重 | 同 `(type,content)` 抽两次 → 第二次返回已有 id，权重加权 | 单元 |
| T9 黑名单 | content 命中黑名单关键词 → `propose` 返回 0 | 单元 |
| T10 端到端 | `Agent(extract_auto=True)` + `FakeLLM` → `reply()` 后候选出现；`False` 时不出现 | 集成 |
| T11 节流 | 连续多轮 `reply()`（自动模式）→ 仅每 `extract_min_interval_turns` 轮抽一次（查 `last_extract_msg_id`） | 集成 |

> 与 Step A 既有 `tests/test_memory_gate.py`（7 项）互补：Step A 测"闸门隔离"，本步新增测"LLM 抽取 → 闸门"全链路 + 配置默认关 + 三柱隔离守卫。

---

## 7. 实施前必须先做的改动（Prerequisite Gaps）

| Gap | 现状 | 本步需补 |
|-----|------|---------|
| G1 | `LLMClient` 只有 `stream_chat` | 加 `chat()` 非流式（DeepSeekLLMClient 同端点 `stream=False`） |
| G2 | `Settings` 无抽取开关 | 加 `extract_auto`/`extract_window`/`extract_max_candidates`/`extract_min_interval_turns` + `.env` 读取 |
| G3 | `Agent` 无抽取接线 | `__init__` 接 `extractor`/`extract_auto`；`reply()` 末尾 `_maybe_extract`；`last_extract_msg_id` 记账 |
| G4 | 无后台/手动触发机制 | 后台线程（自动模式）+ UI「🧹 整理记忆」按钮（手动）二选一或并存 |

## 8. 实施里程碑（确认后）

- **M1 基建**（✅ 已实现，2026-08-12，134 测试通过）：`chat()` 协议签名 + `ExtractionEngine.extract` + `parse_json_array` + FakeLLM + T1/T4/T5/T8/T9 + 三柱隔离守卫。真实 `DeepSeekLLMClient.chat` 网络实现与 `Settings.extract_auto` 留待 M2/M4。
- **M2 接线**：G3 `Agent` 接线 + 默认关 → T7/T10/T11
- **M3 闸门闭环**：G4 手动「🧹 整理记忆」按钮接 `pending_candidates` 确认 UI → T2/T3/T6
- **M4 真实 DeepSeek 验证**：你本机联网，`EXTRACT_AUTO=1` 跑一段对话，看候选质量与误抽率

## 9. 待你确认的问题

1. 自动模式走**后台线程**还是仅**手动「🧹 整理记忆」按钮**？（建议两者并存，默认手动；自动需你显式开 `EXTRACT_AUTO=1`）
2. 抽取节流默认 `extract_min_interval_turns=3` 是否合适？
3. `existing_summary`（把已有记忆喂回模型去重）是否第一版就做，还是先靠 `UNIQUE` 去重？
4. 是否现在就把 G1–G4 全部实现，还是先只做 M1（引擎 + FakeLLM 测试）供你审阅？
