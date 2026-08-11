# 03 · assistant-agent 与目标架构的差异清单（主线）

> 写于 2026-08-05。对照「目标架构」（分层 + 记忆闸门 + 三柱隔离 + 可治理 + 真实数据驱动），
> 列出主线 **已达成** 与 **真实差距**。结论先行：**差距主要在数据，不在设计。**

---

## 1. 已达成（无需再建）

| 目标能力 | 主线现状 | 位置 |
|---------|---------|------|
| 分层：Agent → MemoryManager → Store | ✅ | `core/agent.py` / `manager.py` / `store.py` |
| 记忆闸门 candidate→confirm→long term | ✅ 完整闭环 | `store.memory_candidate` + `manager.propose/confirm_candidate` + UI |
| 无 LLM 自动直写 memory | ✅ | 全仓无此路径 |
| 五通道混合检索 | ✅ | `core/memory/retrieval.py` |
| 三柱隔离（身份/知识/记忆/情绪） | ✅ 知识+记忆已隔离；情绪为 seam | `core/prompt_builder.py` |
| 记忆注入提示词（去分数） | ✅ | `format_memory_block()` |
| UI 记忆管理面板（确认/否决/遗忘/高亮） | ✅ | `ui/components/memory_panel.py` |
| 向量模型可切换 + 模型标记防错配 | ✅ | `store.embedding_model/_dim` + `reindex()` |
| 记忆治理（编辑/遗忘/黑名单） | ✅ | `edit_memory / forget / blacklist` |
| 对话冷启动恢复（记得住上次话题） | ✅ | `MemoryManager.recent_turns` + `Agent._restore_history` |
| 可测试（105 passed） | ✅ | `tests/` |

---

## 2. 真实差距（按严重度）

### G1 · 零真实数据（严重度：高，且是「第一要务」）
- `data/assistant.db` **在磁盘上不存在**，主线从未真正跑起来积累过一条对话。
- 121 条真实对话、7 条长期记忆、1 条画像全部躺在 `C:\Assistant/memory.json`（另一个仓库）。
- 影响：架构再干净也只是空壳；没有数据就无法验证检索/闸门/提示注入在实际语料上的效果。
- 修复方向：把 `C:\Assistant` 的真实对话**迁移**进主线的 `conversation` 表，并以候选方式种子化少量记忆（见 04-M1）。

### G2 · Step 2.7 LLM 抽取器未接入（严重度：高，但闸门已备）
- `propose()` 已就绪，但**没有任何代码**把对话自动变成候选。闸门空转。
- 影响：记忆积累仍100%依赖用户手动「记住」；长期陪伴的记忆密度上不去。
- 修复方向：实现 Extractor，调用 `propose()`；`EXTRACT_AUTO=0` 默认关闭，仅「🧹 整理记忆」手动触发（见 04-M3）。

### G3 · Knowledge RAG 是未提交的 WIP 且「常开」（严重度：中）
- `core/knowledge/` 已实现并接入 `ui/app.py`，但**未 git 提交**（`git status` 显示 `?? core/knowledge/`）。
- 它没有独立 enable 开关：只要 `knowledge/` 目录存在就启用，与用户「暂不实现 Knowledge RAG」的优先级表述不完全一致。
- 影响：不是错误（已做三柱隔离、降级安全），但「暂不实现」的边界没有用开关显式固化。
- 修复方向：加 `KNOWLEDGE_ENABLED`（默认 True 保持现状，但显式声明边界），或按 04-M4 冻结为基线不扩展（见 04-M4）。

### G4 · Emotion 仅 seam（严重度：低，符合预期）
- `PromptBuilder.emotion_block` 永远 `None`，`persona_state.emotion` 恒 `'calm'`。
- 完全符合用户「暂不实现 Emotion」。无需现在动。

### G5 · Relationship / 成长系统（Step 3）未实现（严重度：低，已明确延后）
- 无 stage、无 trust 自动漂移。符合用户「stage 只作背景事实、绝不控制语气」的既定约束。
- 接上时须守约：stage 不进提示词语气分支。

### G6 · 未用 sqlite-vec / 向量索引（严重度：低，设计上主动推迟）
- 检索对候选集做暴力余弦（`candidate_limit=500`），设计文档明确「个人场景千级记忆下开销噪声级，不划算」。
- 替换点是 `MemoryRetriever._vector_scores()` 一个方法，不影响其余打分逻辑。
- 修复方向：仅当数据规模真的上来（> 万级）才引入 sqlite-vec（见 04-M7）。

### G7 · 衰减 / 遗忘策略未启用（严重度：低）
- `decay_rate` / `last_confirmed_at` 已落库；`confirm_memory()` 会刷新锚点，但排序尚未使用。
- 修复方向：Phase 2-C 后启用，利用已埋好的锚点。

### G8 · 未建立提交基线（严重度：中，工程卫生）
- `git status` 显示大量未提交改动：`config.py`、`core/agent.py`、`core/prompt_builder.py`、`ui/app.py` 已改；
  `core/knowledge/`、`knowledge/`、`tests/test_knowledge.py`、`tools/manual_memory_test.py`、`ROADMAP.md` 未跟踪。
- 影响：主线当前没有干净的检查点，迁移途中若出错难以回滚。
- 修复方向：先把这些 WIP 作为「Phase 2 基线」一次性提交，作为 M0（见 04-M0）。

---

## 3. 主线**超出**目标架构的部分（正向，保留）

| 超出点 | 说明 |
|--------|------|
| 候选来源可追溯 | `memory_candidate.source_msg_id` 把候选挂到原始对话，便于审计与回放 |
| memory_control 黑名单 | `memory_blacklist` + `purge_candidates_by_keyword`：用户说「别记这个」能同时清正表与队列 |
| 向量模型标记 | `embedding_model/_dim` 防跨模型错配，换模型后 `reindex()` 重算 |
| 检索可解释 | `RetrievalHit.channels` 五通道分项，Inspector 能解释「为什么想起它」 |
| 写入同步建向量 | `remember()/confirm_candidate()` 写完立即补向量，避免「刚说记住却检索不到」窗口 |

---

## 4. 与 `C:\Assistant` 错误的对照（为什么主线不需要「修复自动写入」）

`C:\Assistant` 旧实现曾存在「LLM 自动总结画像并直写 `user_profile`/memories 桶、无闸门」的污染，
我在上一轮误把它当成主线去实现 2.7/2.8/2.9/Step3。

主线 `assistant-agent` **从未有这个问题**：
- 写入本就走 `propose → confirm`；
- 唯一自动写是 `conversation` 流水（非记忆）；
- 无 LLM 直写 `user_profile` / `memory`。

因此用户要求的「第一优先级：修复自动写入 + 建闸门」在主线**已完成**。
真正要做的见 04：灌数据（G1）+ 接抽取器到闸门（G2）+ 提交基线（G8）。
