# 04 · 推荐迁移路线（主线 assistant-agent，渐进而非重写）

> 写于 2026-08-05。方向：**在现有 assistant-agent 上渐进迁移，不重写**。
> 核心顺序遵循用户既定约束：先积累真实数据、再接抽取器；Knowledge / Emotion 暂不扩展。

---

## 0. 总原则

1. **不推倒重来**：主线架构已正确，目标是「灌数据 + 接通已建好的闸门 + 固化边界」。
2. **数据优先于设计**：先让主线跑起真实对话（M1），再谈抽取器（M3）。
3. **闸门不可绕过**：任何自动记忆（含 2.7 抽取）一律 `propose → 人确认`，`EXTRACT_AUTO=0` 默认关。
4. **每项可独立回滚**：每个里程碑对应一个 git 提交；M0 先建干净基线。

---

## M0 · 立足基线（工程卫生，先做）

- 把当前 WIP 作为「Phase 2 基线」一次性提交：`config.py`、`core/agent.py`、`core/prompt_builder.py`、
  `ui/app.py` 的改动 + `core/knowledge/`、`knowledge/`、`tests/test_knowledge.py`、
  `tools/manual_memory_test.py`、`ROADMAP.md`。
- 目的：给后续迁移一个可回滚的检查点（对应 G8）。
- 验收：`git log` 出现基线提交；`pytest` 仍 105+ passed。
- 注：此步涉及 `git commit`，建议在确认 WIP 内容无误后执行（非自动）。

## M1 · 灌入真实数据（第一要务，对应 G1）

- 写一次性迁移脚本 `tools/import_assistant_mvp.py`：
  - 读 `C:\Assistant/memory.json` 的 121 条对话 → 按时间顺序写入主线 `conversation` 表（保留 role/ts）；
  - 把 7 条长期记忆以 **`propose()` 候选** 方式种子化（**不直接 `remember` 写正表**），
    由用户在 UI「待确认」里逐条点「记住」确认入正表——既补了数据，又验证了闸门；
  - 画像（1 条）按 `user_profile` 写入，标明 `source=migration`，不自动进 memory。
- 目的：让主线从「零数据空壳」变成「有真实语料可验证」。这是用户「先积累真实数据」的直接落地。
- 验收：`data/assistant.db` 生成；`pending_candidates()` 出现迁移候选；`recent_turns()` 能回看历史。
- 风险：MVP 旧数据含误分类（如「助手这么可爱，一切努力都值了」），**以候选形式呈现由人筛除**，不正直写。

## M2 · 记忆可治理打磨（巩固已建能力，对应 G7）

- 复用已有 `edit_memory / forget / blacklist`，补：
  - UI 暴露「遗忘并加入黑名单」一键（已有 `forget()`，加个入口即可）；
  - `memory_inspector.py` 增加「列出 decay 锚点」预览（为 G7 铺路，不启用逻辑）；
  - 迁移候选确认完毕后跑一次 `reindex()` 保证向量齐全。
- 目的：让用户能真正长期管理记忆，而不是只能「记」。

## M3 · 接通 Step 2.7 抽取器（对应 G2，闸门已备）

- 新增 `core/memory/extractor.py`：用 LLM 从最近若干轮对话抽取候选记忆（fact/preference/event/goal/relationship）。
- **强制走 `MemoryManager.propose()`** → `memory_candidate`（pending）→ 用户确认。
- `EXTRACT_AUTO=0` 默认关闭；仅「🧹 整理记忆」手动按钮触发；绝不后台自动直写。
- `source_msg_id` 全量填充（G3 部分达成），便于审计。
- 目的：在守住闸门的前提下提升记忆密度。
- 验收：手动点「整理记忆」后 `pending_candidates()` 出现抽取候选；确认后 `retrieve()` 能命中。

## M4 · Knowledge RAG 边界固化（对应 G3，暂不扩展）

- 加 `KNOWLEDGE_ENABLED`（默认 True，保持现状），把「暂不实现/扩展」的边界用开关显式声明；
- 不扩充语料、`knowledge_top_k` 维持 4；
- 若用户明确要关闭，设 `KNOWLEDGE_ENABLED=0` 即可，无需改代码。
- 目的：让「暂不实现 Knowledge RAG」从口头约定变成可验证的代码状态。

## M5 · Emotion 支柱（延后，用户明确「暂不实现」）

- 仅当 M1~M3 跑稳、真实数据积累一段后；
- 届时 `persona_state.emotion` 才由模块写入，`PromptBuilder.emotion_block` 才接上；
- 情绪只调制语气，**绝不改写身份/知识/记忆三柱内容**。

## M6 · Relationship / 成长系统（延后，对应 Step 3）

- 守约：`stage` 只作背景事实，**绝不控制提示词语气分支**；
- trust 等状态变化必须经人确认或显式交互，不随对话自动漂移（延续主线的「无自动污染」原则）。

## M7 · 向量索引 sqlite-vec（仅当规模需要，对应 G6）

- 当记忆量真的到万级、暴力余弦成为瓶颈时，仅替换 `MemoryRetriever._vector_scores()` 为 sqlite-vec；
- 其余打分逻辑、闸门、提示注入均不动。

---

## 里程碑顺序与依赖

```
M0 基线提交 ──► M1 灌真实数据 ──► M2 治理打磨 ──► M3 接 2.7 抽取(走闸门)
                                              │
                                              └─► M4 固化 Knowledge 边界(并行，低风险)
M5 Emotion（延后）   M6 Relationship（延后）   M7 sqlite-vec（按需）
```

**第一优先级（用户的原话）在本路线里的落点**：
「修复自动写入 + 建候选闸门」= 主线**已完成**（见 01/02），无需 M 步骤；
真正属于「第一优先级精神」的落地动作是 **M1（让闸门有真实数据可管）** 与 **M3（把抽取接到闸门，且不破坏闸门）**。

---

## 与 `C:\Assistant`（MVP）的关系

- `C:\Assistant` 已冻结：其越界实现的 Knowledge/Emotion/Relationship 全部加 feature flag 默认关闭（上一轮已完成，49 passed）。
- 它的价值仅剩「真实数据来源」——通过 M1 迁移脚本把数据喂给主线后即功成身退。
- 迁移完成后 `C:\Assistant` 可归档保留，不再作为开发目标。
