# assistant-agent 开发路线与进度

> 长期陪伴型 AI Agent（助手） · 本地优先 · 不跑本地 LLM
> 维护视角：架构冻结文档 + 实际落地进度 + 未来实现方案

---

## 0. 项目定位与硬约束

| 项 | 选择 |
|----|------|
| LLM | DeepSeek API（云端，不本地跑） |
| Embedding | `bge-small-zh-v1.5` 本地运行（CPU，不占显存） |
| 向量检索 | sqlite-vec（规划中）/ 当前暴力余弦 |
| Memory 存储 | SQLite（`memory` 表） |
| Knowledge 存储 | markdown 语料（`knowledge/*.md`） |
| 开发设备 | Ryzen 7 5800H / 16GB DDR4 / RTX 3050 4GB / 512GB SSD |

**设计铁律**：按低资源本地环境设计，不引入需要大量显存的模型；三层上下文物理隔离、互不混入。

---

## 1. 三层上下文架构（本次设计的核心）

```
角色设定  ──►  Knowledge RAG    knowledge/*.md      → 【角色知识】
用户经历  ──►  Memory           SQLite 记忆表        → 【相关用户记忆】
当前情绪  ──►  Emotion          （seam 已留，待实现） → 【当前情绪】
```

三条原则：
1. **物理隔离** —— Knowledge 只读 markdown，绝不读写 `memory` 表；Memory 只查自己的表。
2. **各自定界** —— Prompt 中三块用独立定界符包裹，顺序固定（身份 → 核心价值观 → 知识 → 记忆 → 关系 → 情绪(seam) → 行为 → 语言 → 护盾）。
3. **防注入** —— 任意检索结果（记忆/知识）注入提示词时**只给内容、不给 score**，避免提示词注入。

---

## 2. 已完成内容

### Phase 1 — 基础 Agent
- 静态人格 Agent：`persona` + `PromptBuilder` + `DeepSeekLLMClient`，纯内存对话闭环。

### Phase 2-A — 长期记忆系统

| 步骤 | 内容 | 提交 |
|------|------|------|
| **2.1 记忆库 schema** | `store.py`：四表（`memory` / `persona_state` / `memory_blacklist` / `memory_candidate`）+ `SCHEMA_VERSION` 迁移机制 | `c0a474e` |
| **2.2 对话接入** | `Agent.add_turn` 落盘；冷启动回填短期窗口；`memory=None` 时退回纯内存（向后兼容） | `4e9f77f` |
| **2.3 手动记忆 + 候选闸门** | `remember` / `forget` / `blacklist` / `propose` / `confirm_candidate` / `reject_candidate`；候选需人工确认才进正表 | `8cb4b40` |
| **2.4 记忆读取闭环** | `embedder.py`（可替换 Embedder：bge-small-zh-v1.5 / 纯 stdlib 哈希回退，blake2b 稳定哈希）；`retrieval.py`（五通道混合打分 vector0.40/keyword0.20/recency0.15/importance0.15/confidence0.10，向量缺失自动重归一化降级）；`MemoryManager.retrieve/memory_block/reindex`；Prompt 定界注入；Agent 用真实检索替换原 `time.sleep` 模拟序列 | `10981cc` |
| **2.6 记忆档案面板** | `MemoryPanel` 真实数据化：三栏分区（长期记忆/近期事件/重要目标）+ 候选确认队列（记住/不用记）+ 每轮检索高亮；清债 #1/#2/#3 | `10981cc` |
| **Step A Memory Gate（抽取器 seam）** | `core/memory/extractor.py`：`MemoryExtractor` + `ExtractedMemory`，自动抽取只经 `propose` 进 `memory_candidate` 闸门、绝不直写正表（源码守卫）；`tests/test_memory_gate.py` 隔离测试（临时信息不进长期记忆 / 稳定偏好进候选 / reject 不污染检索 / 源码守卫 / confirm 转正可检索） | `99da80f` |

### Knowledge RAG 三柱隔离（架构新增，尚未提交）

- `core/knowledge/`：`loader.py`（按标题切块 `KnowledgeChunk`）、`retriever.py`（`KnowledgeHit` + 向量/关键词混合检索 + `render_block` 定界渲染）、`manager.py`（`KnowledgeManager` + `build_knowledge_manager` 工厂，加载失败优雅降级）。
- `knowledge/` 4 个 starter 语料：`assistant_persona.md` / `assistant_world.md` / `relationship_rules.md` / `speaking_style.md`。
- `PromptBuilder.build_system` 扩展为 `build_system(memory_block, *, knowledge_block, emotion_block)` 三柱定界拼装。
- `Agent` 新增 `knowledge` 参数，每轮用用户原话检索知识并作为独立块注入（与记忆并列、不混）。
- `config.py` + `.env.example` 增加 `KNOWLEDGE_DIR` / `KNOWLEDGE_TOP_K`；`ui/app.py` 按配置构建并传入（失败不致命）。
- **测试**：全量 **105 passed**（含 11 项 knowledge 单测）。

### Phase 3 — Persona Engine（人格引擎）

- `core/persona/` 包化：旧 `core/persona.py` 迁入 `persona.py`，新增 `loader.py` / `behavior_rules.py` / `relationship.py` / `persona_manager.py`；兼容旧 import `from core.persona import Persona / load_persona / PersonaManager`。
- `worldview` → 改名为 `perspective`（认知视角）；`relationship_to_doctor` / `behavior_guidelines` 移出 `identity.yaml`，分别落到 `relationship.yaml` / `behavior.yaml`。
- `RelationshipManager`：trust/stage 系统状态管理，读写 `persona_state` 表（与 `memory` 分表，**不污染用户记忆**）；stage v1 四档 **初识 / 熟悉 / 信任 / 深度陪伴**（不使用「家人」）；`block()` 注入【与用户的关系】，仅背景事实、**绝不控语气**。
- `PromptBuilder.build_system` 重写新注入顺序（人格先于上下文，减少漂移）：`身份 → 视角与价值观 → 知识 → 记忆 → 关系 → 情绪(seam) → 行为 → 语言 → 护盾`。
- `Agent` 构造 `PersonaManager`，每轮注入 `relationship_block` / `behavior_block`；`MemoryManager` 新增 `save_state` 透传。
- Emotion / Voice 本轮仅保留 seam，不实现完整逻辑。
- **测试**：全量 **120 passed**（含 12 项 persona 引擎单测）。

> 说明：原冻结文档中 "2.5 Prompt 注入" 已在 2.4 中一并落地（Agent 真实检索后调用 `build_system(memory_block)`），故 2.5 不再单列。

---

## 3. 未来要做的内容

| # | 项目 | 性质 | 当前状态 |
|---|------|------|---------|
| 3.1 | **Step 2.7 LLM 自动抽取** | 进行中 | **M1 + M2.1 已落地**：ExtractionEngine + parse_json_array + FakeLLM（M1，134 测试）；M2.1 完成 Agent 接线（`extractor` 可选依赖注入、EXTRACT_AUTO 默认关、默认/手动窗口 10/20 轮）+ `last_extract_msg_id` 书签幂等（全量 143 测试通过）。真实 DeepSeek `chat()`（M4）/ UI 闸门面板（M3）仍待做 |
| 3.2 | **Emotion 支柱** | 补 `#11` seam | 仅留 `emotion_block=None` 占位 |
| 3.3 | **记忆冲突消解**（Test3：旧 Python / 新 Rust） | 已知缺口 | 新旧记忆共存，旧 confidence 不自动下调 |
| 3.4 | **sqlite-vec 实装** | 性能/架构 | 暴力余弦，替换点是单方法 |
| 3.5 | **UI 打磨** | 体验 | 滑块/增量高亮（债 #6/#7） |
| 3.6 | **提交 Knowledge 三柱隔离** | 工程 | 改动未 commit |
| 3.7 | **真实 bge 语义召回验证** | 验证 | **离线基线已跑通**：沙箱无网→哈希回退，检索管线 + Gate 集成验证 6/6 PASS；真实 bge 语义召回待你本机联网跑（见 4.7） |

---

## 4. 未来实现方案（怎么落地）

### 4.1 Step 2.7 LLM 自动抽取（核心缺口）

**目标**：对话过程中让 DeepSeek 自动从对话里抽出候选记忆，但**绝不直写正表**，仍走人工确认闸门。

- 新增 `core/memory/extractor.py`：`MemoryExtractor`
  - 输入：近 N 轮对话（用户原话 + 助手回复）+ 当前已有记忆摘要
  - 用**独立 system prompt**（"你是记忆整理助手，只输出结构化候选，不要闲聊"）调用 DeepSeek
  - 要求输出 JSON 数组：`[{type, content, importance(1-10), confidence(1-5), reason}]`
- `Agent` 接线：
  - 新增 `auto_extract: bool = False`（默认关，避免污染记忆）+ 抽后调用 `MemoryManager.propose()` 入 `memory_candidate` 闸门
  - `process_queue()` 可选后台跑（不阻塞对话）
- 安全与降级：
  - 抽取失败 / JSON 解析失败 → 静默跳过（宽 except + 日志），绝不崩对话
  - 候选内容做长度/类型校验，非法直接丢弃
- 测试：`FakeLLM` 返回固定候选 → 验证 `propose` 入队、`confirm_candidate` 转正、`reject_candidate` 不污染正表、抽取异常不中断。

### 4.2 Emotion 支柱（补 `#11` seam）

**目标**：让助手"当前情绪"成为第三根上下文支柱。

- 新增 `core/emotion/` 或并入 persona runtime：
  - 每轮用轻量规则 / 一次低成本 LLM 判断情绪标签（平静 / 开心 / 担忧 / 生气 / 温柔 …）
  - 输出 `emotion_block`（如 `【当前情绪】平静而专注`）
- `PromptBuilder` 已有 `emotion_block` 参数 seam，填入即可，顺序保证在【相关用户记忆】之后。
- 范围决策（待定）：短期情绪（仅本轮）即可；长期情绪倾向可转成 `memory`（relationship/event 类型），不单独持久化情绪表。
- 测试：`emotion_block` 定界正确、在三柱中位于记忆之后。

### 4.3 记忆冲突消解（Test3：旧 Python / 新 Rust）

**目标**：用户改了偏好/事实时，旧记忆应被合理降权，而不是和新记忆并列污染召回。

- **方案 A（推荐先做，规则轻量）**：
  - 写入/确认候选时，`MemoryManager` 调 `retriever` 检测同 `type` 且语义相近的旧记忆
  - 对新候选标记 `supersedes=<old_id>`；确认转正时把旧记忆 `confidence` 下调（如 ×0.5）或置 `superseded=1`
  - `memory` 表加 `superseded` 列（schema v4 迁移）
  - 召回阶段对被 superseded 的记忆加权降权
- **方案 B（LLM 主导）**：
  - 在 4.1 抽取阶段就让 DeepSeek 输出 `conflicts_with`（旧记忆 id/内容），由抽取链路负责标记冲突
- 测试：写"改用 Rust"后，原"喜欢 Python"的 `confidence` 下降、recall 加权降低；无冲突时不误伤。
- **决策点**：选 A 还是 B？（建议先 A，低成本、可解释。）

### 4.4 sqlite-vec 实装（替换暴力余弦）

**目标**：把向量检索从内存暴力余弦换成 sqlite-vec 向量索引。

- `store` 初始化时 `enable_load_extension` + `load_extension(sqlite_vec)`（已验证 `enable_load_extension` 可用，sqlite 3.53.1）
- 建虚拟表 `vec_memory(embedding float[dim])`，替代 `memory.embedding` BLOB 列
- `retrieval.py` 的 `_vector_scores` 改为 SQL `knn` 查询（**当前已是单方法替换点**，改动集中）
- 保留暴力余弦作为「无扩展 / 内存」回退分支
- 测试：相同查询两种后端 top_k 与排序一致（允许微小浮点误差）

### 4.5 UI 打磨（清债 #6/#7）

- "记住一件事"对话框加 `importance` / `confidence` 滑块（当前硬编码 5/4，见 `#6`）
- `set_active_memories` 改为就地高亮命中项，而非每轮全量 `refresh()` 重查库（见 `#7`）
- 面板"遗忘"按钮加 tooltip 说明是单条删除（非关键词黑名单），消除命名混淆（`#9`）

### 4.6 提交 Knowledge 三柱隔离

- `git add` 本次 Knowledge 相关全部文件（含 `knowledge/` 语料、`core/knowledge/`、`core/prompt_builder.py`、`core/agent.py`、`config.py`、`.env.example`、`ui/app.py`、`tests/test_knowledge.py`），提交为独立 checkpoint（沿用 `Phase 2-A Step 2.8：Knowledge RAG 三柱隔离` 风格信息）。

### 4.7 真实 bge 语义召回验证

- 你本机联网执行 `python tools/manual_memory_test.py --backend auto`
- harness 现已增强（`0aaf2ff` 之后）：
  - **离线快速回退**：2s 探测 HuggingFace 可达性，离线直接走哈希（不再卡 30s 重试/崩溃）；在线才允许 auto 下载真实 bge。
  - **新增「B. Memory Gate 集成」节**：经真实 `MemoryManager.retrieve → MemoryRetriever` 五通道打分链路验证 Step A 隔离保证（候选确认前绝不进检索 / confirm 后召回 / reject 永不污染），离线可跑。
- 重点验证：
  1. 语义改写召回（"开发习惯" ↔ "规划架构再写代码"）是否生效
  2. Test2 无关项（"今天晚上吃什么"）是否被排除误召回 — 注意：当前五通道含 recency/importance/confidence（与查询无关），记忆极少时无相关性门槛会仍返回全部，真实 bge 只强化 vector 通道；如需「无关严格不召回」见债 #15
  3. Test3 冲突场景的 confidence 行为（依赖 4.3 是否先实现）

---

## 5. 技术债登记（滚动）

| 条目 | 影响 | 计划清理 |
|------|------|---------|
| #1 Agent 直连 retrieval 子层 | 已清（2.6） | — |
| #2 app 检索高亮占位钩子 | 已清（2.6） | — |
| #3 inspector 用 mktemp | 已清（2.6） | — |
| #6 加记忆对话框硬编码 importance/confidence | 低 | 4.5 |
| #7 set_active_memories 全量 refresh | 低 | 4.5 |
| #8 _safe_update 吞 RuntimeError | 低 | 保持（必要） |
| #9 面板"遗忘"语义易混 | 低 | 4.5 |
| #10 knowledge 复用 memory.embedder 原语 | 低 | 拆 `core/embed` 公共层 |
| #11 Emotion 仅 seam | 低 | 4.2 |
| #12 知识检索权重未进 config | 低 | 在线调参时再提 |
| #13 Memory 五通道混合检索权重（vector/keyword/recency/importance/confidence）未调优，沿用经验默认 | 低 | 真实 bge 验证后调参（4.7）；本阶段**刻意不优化** |
| #14 Relationship trust 自动涨跌未实现 | 低 | 后续阶段（`stage` 仅静态读取默认 trust=70，set_trust/bump 方法已就位待调用） |
| #15 检索相关性门槛缺失（记忆极少时无关查询仍召回；recency/importance/confidence 与查询无关，形成打分地板） | 低 | 后续「检索相关性门槛」增强（要求 vector/keyword 至少一项有贡献） |

---

## 6. 开放决策（需要你拍板）

1. **冲突消解**选方案 A（规则轻量）还是 B（LLM 主导）？→ 建议先 A。
2. **Emotion** 是否要做长期持久化，还是仅本轮短期？
3. **auto_extract 默认关已落地（M2.1）**：Agent 接线但 dormant（守卫在 `llm.chat` 之前），手动整理优先；真实自动触发待 M4 验证后考虑默认开。
4. 是否现在就**提交 Knowledge 三柱隔离**改动？→ 已于 Phase 2.7 基线提交（与记忆检索接入合并为一笔）。

---

_最后更新：2026-08-12 · Step 2.7 M1 + M2.1 已落地（ExtractionEngine + Agent 接线 + last_extract_msg_id 书签幂等，全量 143 测试通过；EXTRACT_AUTO 默认关，手动整理优先）。Step A Memory Gate 已落地。Phase 3 Persona Engine 已落地。_
