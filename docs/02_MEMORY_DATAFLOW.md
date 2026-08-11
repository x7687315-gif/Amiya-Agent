# 02 · assistant-agent 当前 Memory 数据流（主线）

> 写于 2026-08-05。描述 `<ASSISTANT_AGENT_DIR>` 主线里「记忆相关的所有数据怎么进、怎么出」。
> 核心结论：**唯一自动写入是对话流水（不是记忆）；记忆写入全部走人工闸门。**

---

## 1. 一句话结论

```
自动写入（无需人确认）          →  conversation 表（对话日志，非记忆，安全）
用户显式「记住这件事」          →  memory 表（用户已确认，安全）
LLM 建议记 / 待确认             →  memory_candidate 表（pending）→ 人确认 → memory 表
LLM 自动直写 memory 正表        →  ❌ 不存在（这正是主线不需要「修复」的原因）
```

---

## 2. 写入路径总表

| # | 入口 | 落点表 | 是否需人确认 | 触发方 | 位置 |
|---|------|--------|------------|--------|------|
| W1 | `Agent._remember(role, content)` | `conversation` | 否（但只是日志） | 每轮对话自动 | `core/agent.py:96` |
| W2 | `MemoryManager.remember(...)` | `memory` | 用户已点「记住」即确认 | 用户（UI 按钮 / 工具） | `core/memory/manager.py:124` |
| W3 | `MemoryManager.propose(...)` | `memory_candidate`(pending) | **需**（下一步） | 未来抽取器 / 工具 / 手动 | `core/memory/manager.py:275` |
| W4 | `MemoryManager.confirm_candidate(cid)` | `memory`（从候选转正） | 用户点「记住」 | 用户（UI / API） | `core/memory/manager.py:309` |
| — | `store.reject_candidate` / `purge_candidates_by_keyword` | `memory_candidate`(rejected) | — | 用户否决 | `store.py:534` |
| — | `MemoryManager.forget` / `forget_id` | `memory`（删）+ `blacklist` | 用户主动遗忘 | 用户（UI） | `manager.py:189` |

**W1 的「自动」是安全的**：它写的是 `conversation`（对话流水），不是 `memory`。
内容经过 `role ∈ {user,assistant}` 校验与空白跳过，且失败只降级（`memory_degraded` 标记），
绝不阻断对话。这是「记得住对话」的最小实现，不是「记忆污染」。

**W2 的「直接写 memory」也是安全的**：它只在用户**显式要求**「让助手记住」时触发
（UI 的「+」→ 填内容 → 点「记住」）。这一动作的本身就是人工确认，因此无需再过候选队列。
黑名单在入口拦截（不是事后过滤）。

**W3→W4 是记忆闸门**：任何「系统建议记」的内容（含未来 Step 2.7 的 LLM 抽取）都只能走
`propose` 进 `memory_candidate`，**绝不直接进 `memory`**，必须经 W4 的人确认才转正。

---

## 3. Memory 数据流图

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 420" font-family="Microsoft YaHei, sans-serif">
  <!-- background -->
  <rect width="680" height="420" fill="#f7f9fb"/>

  <!-- title -->
  <text x="24" y="30" font-size="15" font-weight="bold" fill="#1f2d3d">assistant-agent · Memory 数据流（绿色=安全自动，蓝色=人工闸门）</text>

  <!-- conversation flow -->
  <rect x="24" y="60" width="200" height="64" rx="10" fill="#e8f6ee" stroke="#3aa76d" stroke-width="1.5"/>
  <text x="124" y="86" font-size="12" font-weight="bold" fill="#1f6b45" text-anchor="middle">每轮对话（自动）</text>
  <text x="124" y="106" font-size="11" fill="#2e7d52" text-anchor="middle">Agent._remember</text>

  <rect x="250" y="60" width="150" height="64" rx="10" fill="#ffffff" stroke="#3aa76d" stroke-width="1.5"/>
  <text x="325" y="86" font-size="12" font-weight="bold" fill="#1f6b45" text-anchor="middle">conversation 表</text>
  <text x="325" y="106" font-size="10" fill="#2e7d52" text-anchor="middle">对话日志（非记忆）</text>

  <path d="M224 92 L250 92" stroke="#3aa76d" stroke-width="2" marker-end="url(#a)"/>

  <!-- manual remember -->
  <rect x="24" y="170" width="200" height="64" rx="10" fill="#eef4ff" stroke="#4a7fe0" stroke-width="1.5"/>
  <text x="124" y="196" font-size="12" font-weight="bold" fill="#2d55a8" text-anchor="middle">用户「记住这件事」</text>
  <text x="124" y="216" font-size="11" fill="#3a5fb0" text-anchor="middle">MemoryManager.remember</text>

  <rect x="250" y="170" width="150" height="64" rx="10" fill="#ffffff" stroke="#4a7fe0" stroke-width="1.5"/>
  <text x="325" y="196" font-size="12" font-weight="bold" fill="#2d55a8" text-anchor="middle">memory 正表</text>
  <text x="325" y="216" font-size="10" fill="#3a5fb0" text-anchor="middle">已确认（用户触发）</text>

  <path d="M224 202 L250 202" stroke="#4a7fe0" stroke-width="2" marker-end="url(#a)"/>

  <!-- propose gate -->
  <rect x="24" y="280" width="200" height="64" rx="10" fill="#fff4e6" stroke="#e0922a" stroke-width="1.5"/>
  <text x="124" y="300" font-size="11" font-weight="bold" fill="#9a5a10" text-anchor="middle">LLM 抽取 / 系统建议</text>
  <text x="124" y="320" font-size="11" fill="#9a5a10" text-anchor="middle">MemoryManager.propose</text>

  <rect x="250" y="280" width="150" height="64" rx="10" fill="#ffffff" stroke="#e0922a" stroke-width="1.5"/>
  <text x="325" y="300" font-size="12" font-weight="bold" fill="#9a5a10" text-anchor="middle">memory_candidate</text>
  <text x="325" y="320" font-size="10" fill="#9a5a10" text-anchor="middle">pending（待确认）</text>

  <path d="M224 312 L250 312" stroke="#e0922a" stroke-width="2" marker-end="url(#a)"/>

  <!-- confirm / reject -->
  <rect x="430" y="256" width="120" height="44" rx="10" fill="#e8f6ee" stroke="#3aa76d" stroke-width="1.5"/>
  <text x="490" y="283" font-size="11" font-weight="bold" fill="#1f6b45" text-anchor="middle">人点「记住」</text>
  <rect x="430" y="310" width="120" height="44" rx="10" fill="#fdeaea" stroke="#d9534f" stroke-width="1.5"/>
  <text x="490" y="337" font-size="11" font-weight="bold" fill="#a3342f" text-anchor="middle">人点「不用记」</text>

  <path d="M400 300 L430 278" stroke="#3aa76d" stroke-width="2" marker-end="url(#a)"/>
  <path d="M400 320 L430 332" stroke="#d9534f" stroke-width="2" marker-end="url(#a)"/>

  <path d="M550 278 L600 278 L600 202 L400 202" stroke="#3aa76d" stroke-width="2" fill="none" marker-end="url(#a)"/>
  <text x="600" y="244" font-size="10" fill="#1f6b45" text-anchor="middle">转正</text>

  <path d="M550 332 L600 332 L600 232 L250 232" stroke="#d9534f" stroke-width="1.5" stroke-dasharray="4 3" fill="none" marker-end="url(#a)"/>
  <text x="430" y="356" font-size="9" fill="#a3342f">rejected 后同内容不再打扰</text>

  <!-- read side -->
  <rect x="470" y="120" width="186" height="64" rx="10" fill="#f3eefe" stroke="#8a5cd1" stroke-width="1.5"/>
  <text x="563" y="146" font-size="12" font-weight="bold" fill="#5b32a0" text-anchor="middle">MemoryManager.retrieve</text>
  <text x="563" y="166" font-size="10" fill="#6a3fb0" text-anchor="middle">五通道混合打分</text>
  <text x="563" y="182" font-size="9" fill="#6a3fb0" text-anchor="middle">→ format_block（去分数）→ 注入提示词</text>

  <path d="M400 202 L470 152" stroke="#8a5cd1" stroke-width="1.5" stroke-dasharray="5 3" marker-end="url(#a)"/>
  <text x="430" y="176" font-size="9" fill="#5b32a0">读</text>

  <!-- legend -->
  <rect x="24" y="372" width="14" height="14" rx="3" fill="#3aa76d"/><text x="44" y="383" font-size="10" fill="#444">安全自动</text>
  <rect x="120" y="372" width="14" height="14" rx="3" fill="#4a7fe0"/><text x="140" y="383" font-size="10" fill="#444">人工确认写入</text>
  <rect x="240" y="372" width="14" height="14" rx="3" fill="#e0922a"/><text x="260" y="383" font-size="10" fill="#444">待确认闸门</text>
  <rect x="360" y="372" width="14" height="14" rx="3" fill="#8a5cd1"/><text x="380" y="383" font-size="10" fill="#444">检索/读取</text>

  <defs>
    <marker id="a" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
      <path d="M0,0 L6,3 L0,6 Z" fill="#666"/>
    </marker>
  </defs>
</svg>
```

图例：绿色=安全自动（仅对话日志），蓝色=用户显式确认写入，橙色=待确认闸门，紫色=读取/检索。
**没有任何一条路径把「系统建议」直接送进 `memory` 正表而不经过橙色闸门。**

---

## 4. 读取路径（记忆如何被「想起来」）

1. 每轮 `Agent.reply()` 用用户本轮的话调 `MemoryManager.retrieve(query, top_k=MEMORY_TOP_K)`；
2. `MemoryRetriever` 对候选集做五通道混合打分（vector 0.40 / keyword 0.20 / recency 0.15 / importance 0.15 / confidence 0.10，按可用通道归一化）；
3. 命中经 `format_memory_block()` 渲染成「`- (类型) 内容`」段落，**刻意剔除分数**（0.83 这类数字塞进提示词会被模型当成要解释的内容）；
4. `PromptBuilder.build_system(memory_block=...)` 把段落定界注入【相关用户记忆】块；
5. 命中项经 `on_retrieval` 回调交给 UI `MemoryPanel.set_active_memories()` 高亮；
6. 检索失败 / 嵌入不可用 → 只降级为空结果，绝不崩对话。

---

## 5. 关键安全属性（对照用户第一优先级）

| 要求 | 主线现状 | 证据 |
|------|---------|------|
| 不自动直写 memory | ✅ 满足 | 无代码把 LLM 输出写进 `memory` |
| 引入 candidate→confirm→long term 闸门 | ✅ 满足 | `memory_candidate` 表 + `propose/confirm_candidate/reject_candidate` + UI 确认/否决 |
| 闸门闭环（有出口） | ✅ 满足 | `confirm_candidate` 把 pending→memory 并补向量；rejected 不再打扰 |
| 候选可追溯来源 | ✅ 部分 | `source_msg_id` 字段已建，但目前仅手动/工具填，抽取器接入后才全量 |
| 画像无闸门直写 | ✅ N/A | 主线 `user_profile` 仅工具/手动写，无 LLM 自动总结 |

---

## 6. 尚未接通的（不是 bug，是路线未到）

- **Step 2.7 LLM 抽取器**：`propose()` 已就绪，但**还没有任何代码调用它**去把对话自动变成候选。
  闸门空转，等抽取器接上。接入时务必 `EXTRACT_AUTO=0` 默认关闭，只走「🧹 整理记忆」手动按钮 → 候选 → 用户确认。
- **Emotion 支柱**：`PromptBuilder` 有 `emotion_block` 参数但永远传 `None`，`persona_state.emotion` 恒为 `'calm'`，
  无情绪模块。这是预留 seam，符合「暂不实现 Emotion」。
- **衰减 / 遗忘策略**：`decay_rate`、`last_confirmed_at` 字段已落库但逻辑未启用；`forget()` 是显式遗忘，无时间衰减。

---

## 7. `persona_state` 不是记忆

`persona_state`（trust / companionship_* / emotion / total_turns / last_extract_msg_id）记录的是
**Agent 自身的状态**，与「关于用户的回忆」严格分表。它只被 `bump_turns`（用户发言时累加）、
`bump_companionship`（陪伴计时）、`save_state` 改写——**没有任何代码自动改 trust 或 emotion**，
因此不存在「关系随对话自动漂移」的污染。这正是与 `C:\Assistant` 旧实现（曾 LLM 直写画像）的本质区别。
