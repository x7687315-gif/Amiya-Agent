# Step 2.7 · M3 设计：记忆管理中心（Memory Management Center）

> 状态：设计稿（本次重新定义）。前置：M1 `49970aa`（抽取引擎）、M2.1 `951c004`（Agent 接线，手动 extract_now 已可用）。
> 本文固化「重新定义后的 M3」范围，作为实现基线。实现完成后补提交记录。

---

## 0. 一句话结论

把右侧原本的「记忆档案」面板升级为 **记忆管理中心（Memory Management Center）**：
一个**人拥有最终控制权**的透明界面——AI 只「提议」，人「确认 / 修改 / 拒绝」；
同时把**已确认的记忆**按 事实 / 偏好 / 目标 / 经历 / 关系 五类明文陈列，
解决陪伴型 AI 最大的隐患：**用户不知道 AI 记住了什么**。

核心不变量（贯穿本步）：**AI 提议，人拍板**。UI 不允许任何「AI 自行修改/删除记忆」的路径。

---

## 1. 重新定义的范围（Compared to 原 M3）

原 M3 被定义为「UI 整理记忆按钮」——一个触发自动抽取的按钮。现**作废该命名与定位**，改为：

- **新名字**：`Memory Management Center` / 记忆管理中心（界面标题用中文「记忆管理中心」）。
- **不是**一个"一键整理"按钮，而是一个**常驻的、以审阅与控制为中心的面板**。

### 1.1 明确不做（Out of scope）

| 不做 | 原因 |
|------|------|
| ❌ 自动推荐删除 | 删除决策必须来自人；AI 不得自行提议"该删哪条" |
| ❌ AI 自动修改记忆 | 记忆正表只有「人确认 / 人修改」两条入口；抽取引擎绝不直写 |
| ❌ 复杂可视化 | 不画关系图 / 时间轴 / 词云；保持列表式、轻量、可读 |
| ❌ 多用户系统 | 单用户陪伴场景；不引入账户 / 权限 / 多 profile |

### 1.2 明确要做（In scope）

- **M3.1 候选查看**：`memory_candidate`（`status='pending'`）→ UI 陈列，含类型、内容、原因（为什么记得）、权重。
- **M3.2 三个操作**（每条候选必备）：
  - **确认**：`confirm_candidate` → 转正进 `memory` 正表（可被检索）。
  - **修改**：人编辑 AI 的草稿（类型 / 内容 / 重要性 / 置信度），改完**仍 pending**，由人决定下一步。
  - **拒绝**：`reject_candidate` → 标记 `rejected`，同内容永不复发。
- **M3.3 正式记忆查看**：新增「我的记忆」，按 **事实 / 偏好 / 目标 / 经历 / 关系** 五类分栏陈列已确认记忆；每条可**修改 / 遗忘**（人拥有最终控制权）。

> 理念横幅（UI 文案）：「助手会提议该记住什么，但每一条都由你拍板——确认、修改或拒绝。」

---

## 2. 数据流（不变式保持）

```
memory_candidate(status='pending')
   ├─ 确认 → confirm_candidate → memory 正表（可检索）+ 立即补向量
   ├─ 修改 → update_candidate（仍 pending，内容可被人改写）
   └─ 拒绝 → reject_candidate → status='rejected'（永不进正表）

memory（已确认）
   ├─ 查看 → 五分类陈列（事实/偏好/目标/经历/关系）
   ├─ 修改 → edit_memory（人改，正文变更作废旧向量交由 reindex）
   └─ 遗忘 → forget_id（删单条，不进黑名单）

人触发「让助手整理候选」→ Agent.extract_now() → ExtractionEngine.extract()
   → memory_candidate（pending）  ← AI 只提议，下一步仍走 M3.2 人决策
```

三柱隔离不变：M3 全部走 `MemoryManager` 中间层，UI 不碰 store/SQL；抽取仍只写 `memory_candidate`，确认前不可召回。

---

## 3. 数据层改动（最小增量）

### G1 `store.update_candidate`（新增）

```python
def update_candidate(self, cand_id, *, type=None, content=None,
                     importance=None, confidence=None) -> bool:
    # 仅 status='pending' 可改；body 改完仍 pending（不转正、不丢弃）
    # UNIQUE(type, content) 冲突 → sqlite3.IntegrityError → rollback → 返回 False
```

`MemoryStore` 协议补 `update_candidate` 签名；`MemoryManager.update_candidate` 做类型校验 + 权重钳制（`_clamp`） + 黑名单拦截（入口防御）。

> 为什么新增独立方法而非复用 `add_candidate`：修改的是"已存在且 pending"的草稿，
> 语义是 UPDATE BY ID，不是 INSERT/UPSERT；混用会破坏 UNIQUE 去重契约。

---

## 4. UI 结构（ui/components/memory_panel.py）

```
记忆管理中心  [让助手整理候选] [+ 记住一件事]
┌─ 待你确认 ──────────────────────────────┐
│  ℹ️ 助手会提议该记住什么，但每一条都由你拍板… │
│  • [badge 类型] 内容「为什么记得：…」  [确认][修改][拒绝] │
│  • …                                        │
└──────────────────────────────────────────┘
我的记忆
┌─ 事实 ─────┐  • [badge] 内容 [改][忘]
┌─ 偏好 ─────┐  • …
┌─ 目标 ─────┐  • …
┌─ 经历 ─────┐  • …
┌─ 关系 ─────┐  • …
```

- 候选卡片：`确认 / 修改 / 拒绝` 三按钮（M3.2）。
- 「修改」候选：弹出对话框（类型下拉 + 内容 + 重要性 1–10 + 置信度 1–5），保存走 `update_candidate`，**仍 pending**。
- 我的记忆五栏：每栏按 `type` 过滤已确认记忆；每条 `[改][忘]`（人控制）。
- 记忆功能未启用（`MEMORY_ENABLED=0`）：显示明确空态，不假装工作（沿用现有）。
- `on_extract` 回调：可选，仅 extractor 可用时显示「让助手整理候选」按钮；点击经 `page.run_thread` 调 `Agent.extract_now`，完成后 `refresh()` + snackbar。

---

## 5. 测试计划

| 用例 | 验证点 |
|------|--------|
| T1 update_candidate 改内容仍 pending | `update_candidate` 后 `candidate(cid)['status']=='pending'`，内容更新 |
| T2 update_candidate 越界类型/权重钳制 | manager 层 `_check_type` + `_clamp` |
| T3 update_candidate UNIQUE 冲突返回 False | 改成已存在的 pending 同 (type,content) → False |
| T4 修改后确认写正表 | `update_candidate` 再 `confirm_candidate` → memory 内容与修改一致 |
| T5 UI 五栏结构 | 构造含 5 类记忆的 MemoryManager，断言每栏 ≥1 条 |
| T6 UI 候选三操作 | 候选卡片含 确认/修改/拒绝 三个回调入口 |
| T7 未启用空态 | `memory=None` 时只显示一条空态提示 |

> 不引入"自动删除建议 / AI 自动改记忆"任何代码路径；三柱隔离守卫沿用既有测试。

---

## 6. 实施里程碑

- **M3（本步）**：数据层 `update_candidate` + 面板重构为记忆管理中心 + app 接线整理候选回调 + 测试，提交。
- 后续（不在本步）：M4 真实 DeepSeek 联网验证候选质量；候选批量操作（如"全部拒绝"仍为显式人决策）。
