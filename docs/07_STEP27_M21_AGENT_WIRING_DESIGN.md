# Step 2.7 · M2.1 设计：Agent 接入 ExtractionEngine（自动模式默认关闭）

> 本文档**只做设计**，不修改任何源码。等你确认后，我再进入 M2.1 实现。
> 前置：M1 已提交 `49970aa`（ExtractionEngine + 解析 + 测试，见 `docs/06_STEP27_LLM_EXTRACTION_DESIGN.md`）。

---

## 0. 一句话结论

把 `ExtractionEngine` 以**可选依赖**的方式注入 `Agent`：自动抽取钩子 `_maybe_extract()` 在 `reply()` 末尾被调用，但被 `EXTRACT_AUTO` 开关**前置拦截**（False 时直接 return，**不调 LLM、不写候选**）；同时提供手动入口 `extract_now()` 供未来「🧹 整理记忆」按钮调用，它**不受 EXTRACT_AUTO 约束**，只受 `extractor 是否为 None` 约束。

核心不变式：**自动模式关着时，M2.1 对现有行为零影响**——既不多一次 LLM 调用，也不会往 `memory_candidate` 写任何东西。

---

## 1. 影响分析（Impact Analysis）

### 1.1 事实核查（来自真实源码）

| 项 | 现状（M1 之后） | 来源 |
|----|----------------|------|
| `ExtractionEngine` 构造 | `__init__(self, extractor: MemoryExtractor, llm: LLMClient)` | `core/memory/extraction_engine.py:141` |
| `ExtractionEngine.extract` | `extract(self, window: List[Dict[str,str]], *, max_candidates=5) -> List[int]`；内部 `llm.chat(...)` → `parse_json_array` → `extractor.propose_many`，异常静默降级 `[]` | `extraction_engine.py:145-171` |
| `Agent.__init__` 签名 | `(persona, llm, history_limit=20, on_phase, memory, restore_history, on_retrieval, memory_top_k, knowledge, knowledge_top_k)` —— **无 extractor / extract_auto** | `core/agent.py:36-48` |
| `Agent._history` 类型 | `List[Dict[str, str]]`（role/content）—— **与 `extract()` 入参同构**，可直接切片喂入 | `agent.py:72, 116` |
| `Agent.reply` 流程 | user 落盘 → 检索 → 知识 → 拼提示词 → 流式 → assistant 落盘 → trim | `agent.py:114-175` |
| `Settings` | **无** `extract_auto` 字段 | `config.py:31-47` |
| `persona_state.last_extract_msg_id` | schema 已建、字段已入 `_STATE_FIELDS`，但**无人读写**（去重书签，预留） | store.py / manager.py |

### 1.2 本次 M2.1 改动文件

| 文件 | 改动 | 风险 | 向后兼容 |
|------|------|------|----------|
| `core/config.py` | `Settings` 增 `extract_auto: bool`；`load_settings()` 增 `_env_bool("EXTRACT_AUTO", False)` | 低（纯增量字段 + 默认值 False） | ✅ 现有调用方不受影响 |
| `core/agent.py` | `__init__` 增关键字参数 `extractor=None, extract_auto=False`；新增私有 `_maybe_extract()` + 公开 `extract_now()`；`reply()` 末尾调 `_maybe_extract()` | 中（接线逻辑，但全在守卫后） | ✅ 位置参数顺序不变，`extractor` 默认 None → 旧调用方行为不变 |
| `core/memory/__init__.py` | **不改**（M1 已导出 `ExtractionEngine`） | — | ✅ |
| 应用入口（如 `app.py`/`main.py`） | 构造 `Agent` 时传入 `extractor=ExtractionEngine(MemoryExtractor(memory), llm)`、`extract_auto=settings.extract_auto` | 低（仅接线） | ✅ 缺省仍为 None |
| `tests/test_agent_extraction_wiring.py` | 新增 | — | ✅ |

### 1.3 不改动（明确划界）

- **不改** `ExtractionEngine` / `parse_json_array`（M1 已定型）。
- **不改** `MemoryManager.retrieve` / `propose` / `confirm_candidate`（Step A 已定型）。
- **不接** UI 闸门面板（留 M3）。
- **不实现** `last_extract_msg_id` 书签去重（留 M2.2，DB 层 `UNIQUE(type,content)` 已在 propose 时兜底）。
- **不实现** `DeepSeekLLMClient.chat()` 真实网络调用（M1 已声明协议；M2.1 测试用 `FakeLLM`）。

---

## 2. 数据流变化（Data Flow）

### 2.1 现有（M1，无抽取）

```
user_text
  │
  ▼
Agent.reply()
  ├─ _history.append(user)
  ├─ _remember(user)                ──► memory.turns（对话流水）
  ├─ retrieve(user_text)            ──► 读 memory（长期正表）→ 注入提示词
  ├─ knowledge.retrieve()           ──► 读 knowledge/*.md → 注入提示词
  ├─ stream_chat(system, window)    ──► 助手回复（流式）
  ├─ _remember(assistant)
  └─ _trim_history()
```

### 2.2 M2.1（EXTRACT_AUTO = **false**，默认）

```
user_text
  │
  ▼
Agent.reply()  （同上）
  │
  └─ 末尾 _maybe_extract()
        │  if not extract_auto or extractor is None:  return   ◄── 守卫：直接返回
        ▼ （不执行以下任何步骤）
       [ 无 LLM 调用 / 无候选写入 ]
```

> **结论**：EXTRACT_AUTO=false 时，数据流与 M1 **逐字节等价**——多出来的只是一个 O(1) 的布尔判断。

### 2.3 M2.1（EXTRACT_AUTO = **true**，未来开启时）

```
Agent.reply() 末尾 _maybe_extract() 通过守卫
  │
  ├─ window = _history[-6:]                         ──► 复用短期窗口（中性 role/content，非助手提示词）
  ▼
ExtractionEngine.extract(window)
  ├─ llm.chat(EXTRACTION_SYSTEM_PROMPT, window)      ──► 独立抽取提示词（"你不是助手"）
  ├─ parse_json_array(raw)                           ──► 稳健解析，失败→[]
  └─ extractor.propose_many(items)                   ──► memory_candidate（pending）
        │
        ▼
  [人工闸门]  UI「🧹 整理记忆」/ 候选队列 → confirm / reject
        │
        ├─ confirm_candidate ──► memory（正表，可被 retrieve 召回）
        └─ reject_candidate  ──► rejected（永不进检索）
```

### 2.4 手动路径（M2.1 即生效，不受 EXTRACT_AUTO 约束）

```
UI「🧹 整理记忆」按钮（M3 接） 或 测试
  │
  ▼
Agent.extract_now(window_turns=6)
  ├─ if extractor is None: return []
  ├─ window = _history[-12:]
  ▼
ExtractionEngine.extract(window)  ──► 同上 → memory_candidate（pending）
```

> 手动路径是 §9「手动整理优先」的直接落地：即便 `EXTRACT_AUTO=false`，用户也能主动触发整理。

---

## 3. 为什么这样设计（Why this way）

### 3.1 为什么用「可选依赖 + 关键字参数」注入，而不是直接在 reply 里 new 一个引擎？

**设计目的**：让抽取能力成为 Agent 的**可插拔部件**，而非硬耦合。

**解决的工程问题**：
- 向后兼容——现有 30+ 个 `Agent(...)` 测试与调用点（含 `memory=None` 的纯内存模式）**一个都不用改**。如果直接在 `reply()` 内 `ExtractionEngine(MemoryExtractor(self._memory), self.llm)`，则每条测试都必须备好 llm/memory，破坏性极大。
- 单一职责——Agent 只负责"在合适时机触发抽取"，引擎的构造与失败处理仍归引擎自身。
- 测试友好——`extractor=None` 时 `_maybe_extract` 直接 return，构造 Agent 无需任何 LLM 替身。

**为什么不是** `def reply(): ...; ExtractionEngine(MemoryExtractor(self._memory), self.llm).extract(window)`：
那样把"构造 + 触发"焊死在每次对话里，既无法关闭、也无法替换为 FakeLLM 测试，更无法让手动按钮复用同一实例。

### 3.2 为什么 EXTRACT_AUTO 默认 False，且守卫在 LLM 调用之前？

**设计目的**：把"自动写候选"这个有成本、有风险的行为，默认**断电**。

**解决的工程问题**：
- **成本**：自动抽取 = 每轮额外一次 LLM round-trip（DeepSeek 计费 + 延迟）。默认关，开发/回归期零开销。
- **安全（三柱隔离）**：即便未来误开，守卫 `if not extract_auto: return` 在 `llm.chat` **之前**拦截——保证 False 时**绝不发生**任何抽取调用，长期记忆零污染风险。
- **渐进交付**：先交"接线 + 手动触发"（M2.1），把"自动触发"当作一个只翻开关就能亮的灯，留到 M2.2 验证无误再默认开。

### 3.3 为什么自动钩子挂在 reply() 末尾、且复用了 `_history`？

**设计目的**：抽取要"看见"刚刚这轮完整对话（含助手回复），且窗口来源唯一。

**解决的工程问题**：
- 时机——`reply()` 在流式结束后才 `_history.append(assistant)`（agent.py:172），所以**末尾**触发才能让抽取窗口包含本轮助手话；若放在流式前，会漏掉刚说的话。
- 单一事实源——`_history` 本就是喂给助手的短期上下文（`List[Dict[str,str]]`），与 `extract()` 入参**同构**，切片即喂，无需额外检索或重构。避免"对话上下文"出现两份副本。
- 不污染三柱——喂给抽取引擎的是**中性 role/content 字典**，绝不混入助手 system prompt / persona / knowledge 内容；抽取 LLM 用 M1 的独立提示词。Persona、Knowledge 两柱在此路径上**完全不被读取或写入**。

### 3.4 为什么手动 `extract_now()` 不受 EXTRACT_AUTO 约束？

**设计目的**：落实 §9「手动整理优先，自动模式预留」。

**解决的工程问题**：默认 `EXTRACT_AUTO=false` 时，用户仍能主动整理记忆——手动路径是"人主动发起"，风险可控、意图明确，不需要被全局开关拦住。自动路径才是"AI 自作主张"，必须默认关。两者解耦，互不牵连。

### 3.5 三柱隔离如何在此设计中保持

| 柱 | M2.1 中的角色 | 保证 |
|----|--------------|------|
| **Memory** | 唯一写目标，且只经 `ExtractionEngine → MemoryExtractor.propose_many → memory_candidate` | 物理上不经过 `upsert_memory`/`remember`；`retrieve` 只读 `memory` 表，候选在确认前结构上不可召回 |
| **Knowledge** | 不参与 | `ExtractionEngine` 不 import `knowledge` 写路径；`extract_now` 不传 knowledge 内容 |
| **Persona** | 不参与（不读不写） | 抽取窗口是中性字典；抽取用 M1 独立提示词，助手人格不泄漏；`persona_state` 不在此路径写 |

---

## 4. 实现步骤拆分（不要一次性做全）

为遵守「不要一次性实现全部功能」，M2.1 只交付**最小可验证接线**：

- **M2.1（本设计，待确认）**
  1. `config.py`：增 `extract_auto`（默认 False）+ 读取。
  2. `agent.py`：增 `extractor=None, extract_auto=False` 关键字参；新增 `_maybe_extract()`（守卫后调用 `extract`）；新增 `extract_now()`；`reply()` 末尾调 `_maybe_extract()`。
  3. 应用入口：构造 Agent 时传入 `extractor=ExtractionEngine(MemoryExtractor(memory), llm)`（让手动路径可活）。
  4. 测试：`test_agent_extraction_wiring.py`（见 §6）。

- **M2.2（后续，不在本设计）**：`last_extract_msg_id` 书签去重（跨轮不重复处理同一窗口）+ 后台线程选项（自动模式不阻塞回复）。
- **M3（后续）**：UI「🧹 整理记忆」按钮 → `extract_now()` → 候选确认队列面板。
- **M4（后续）**：`DeepSeekLLMClient.chat()` 真实实现 + 联网验证。

---

## 5. 风险点（Risk Register）

| 编号 | 风险 | 概率 | 缓解 |
|------|------|------|------|
| R1 | 守卫位置错误（写在 LLM 调用之后）→ False 时仍计费/污染 | 低 | 守卫 `if not self._extract_auto or self._extractor is None: return` 必须位于 `llm.chat` **之前**；测试 T1 断言 False 时 `FakeLLM.chat` **不被调用** |
| R2 | `reply()` 是生成器，抽取若在流式中触发会看不到助手话 | — | 抽取挂在 `reply()` 末尾（流式结束后），见 §3.3 |
| R3 | 窗口包含被 trim 掉的历史 → 抽取视角与对话视角不一致 | 低 | 窗口从 `_history` 实时切片，trim 在抽取前已完成，二者同源 |
| R4 | `extractor=None` 时仍调 `extract` → AttributeError | 低 | 两入口均先判 `None` 再调 |
| R5 | 自动模式开启后每轮加一次 LLM 延迟，体感卡顿 | 中（仅开启后） | M2.1 默认关；M2.2 起自动路径移后台线程 |
| R6 | 抽取窗口过大 → token 浪费 / 噪声增多 | 低 | `window_turns=3`（自动）/ `6`（手动）可配；`max_candidates=5` 截断 |

---

## 6. 测试方案（Test Plan）

新增 `tests/test_agent_extraction_wiring.py`，用 `FakeLLM`（M1 已有）驱动，**不联网**：

| 用例 | 验证点 | 期望 |
|------|--------|------|
| T1 自动关·零副作用 | `extract_auto=False`，`reply()` 一轮 | `FakeLLM.chat` **从未被调用**；`pending_candidates()` 为空；回复正常产出 |
| T2 自动开·入队 | `extract_auto=True` + `FakeLLM` 返回合法 JSON | 一轮后 `pending_candidates()` 含该候选；`retrieve` 在 confirm 前读不到 |
| T3 手动·不受开关约束 | `extract_auto=False`，显式调 `extract_now()` | `FakeLLM.chat` 被调用；候选入队 |
| T4 extractor=None 安全 | 构造 `Agent(extractor=None)`，自动/手动均调 | 不抛错；无候选 |
| T5 解析失败降级 | `FakeLLM` 返回乱码 | `extract_now()` 返回 `[]`；对话不崩（沿用 M1 parse 降级） |
| T6 向后兼容 | 旧式 `Agent(persona, llm, memory=...)` 位置调用 | 行为同 M1，无 TypeError（关键字参数保证） |
| T7 窗口含助手话 | 手动触发后检查入队候选来源窗口 | `extract()` 收到的 window 末条 role=assistant（本轮回复在内） |

**回归**：全程跑 `.venv/Scripts/python -m pytest`，确保 134 → 141 passed 无回归。

---

## 7. 待确认问题

1. M2.1 范围是否如上（只接线 + 手动入口 + 测试，自动默认关）？还是要把 `last_extract_msg_id` 书签也一并做掉？
2. 自动抽取窗口 `window_turns=3`、手动 `=6` 是否合适？还是统一一个值？
3. 应用入口（请确认是哪个文件：`ui/app.py` 还是 `main.py` 或 `ui/main.py`）需要我一并接 `extractor=...` 吗？还是只改 `Agent` 类、入口留你之后接？
4. 确认后，我按 M2.1 实现并跑测试，再交你 code-check 审阅（同 M1 节奏）；**不**顺手做 M2.2/M3。
