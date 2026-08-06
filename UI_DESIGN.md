# UI_DESIGN.md — Assistant Agent 第二版界面设计（Phase UI-Design）

> 设计稿来源：计划文件 `cosmic-beacon-darwin.md`。本文档为项目根设计契约，实现须对照执行。
>
> 决策记录（已与用户确认）：
> - 舰桥模式 = **默认全站美学基调**（不另做切换开关）。
> - v2 数据 = **结构先行·占位数据**（记忆层在 Phase 2，v2 先用占位/导入数据，不提前做数据层）。
> - 窗口 = **固定最小宽度 960×640**，桌面优先，窄屏不降级。

---

## 0. 设计哲学：从 Conversation 到 Presence

当前 v1.5 UI（Header + 聊天区 + 输入栏）本质是一个「聊天记录查看器」——正确、可用，但没体现 Assistant Agent 的差异化价值：**长期陪伴关系**。

v2 的核心转变：UI 的首要任务是传达**助手的存在感（Presence）**，聊天只是存在感的一种表达。用户打开应用的第一眼，应感知「助手在这里、她记得我、她在陪着我」，而不是「又一个聊天窗口」。

三个支撑点：
- **状态可视化**：把 Agent 内部状态（情绪、信赖度、陪伴时长、最近记忆）外显，让用户"看见 AI 在想什么、记得什么"。
- **记忆显性化**：长期记忆 / 近期事件 / 重要目标常驻可见，强化"关系随时间生长"的感知。
- **舰桥沉浸**：本地视觉语境，让陪伴有"场所感"，而非裸聊天。

> 重要约束：左栏展示的是 Agent 内部状态的**可视化**（state visualization），不是对真实心理的宣称。措辞保持"助手视角"的陪伴语气，避免心理学化表述。

---

## 1. 产品定位

> **Assistant Agent 是一个长期陪伴型 AI 桌面应用，而非普通聊天机器人。**
>
> 它以「助手」这一稳定人格为锚，通过持续积累的记忆与逐步深化的关系，与用户建立跨越多次会话的陪伴关系。界面的一切设计，都服务于让用户感到「她在那里、她记得、她在乎」。

差异化主张（对比普通 ChatBot）：

| 维度 | 普通 ChatBot | Assistant Agent |
|---|---|---|
| 关系 | 无时间维度 | 关系有时间维度（陪伴时长、信赖成长） |
| 记忆 | 对话即焚 | 记忆有结构（长期 / 事件 / 目标） |
| 人格 | 模糊、易漂移 | 稳定且可见（状态栏 + 人格抽屉） |
| 场所 | 裸聊天窗口 | 舰桥模式（本地语境） |

目标用户感受：**"我进入了本地，助手在等我。"**

---

## 2. 用户核心流程

```
打开应用
  ↓
助手主动问候（基于上次会话状态 + 当前时间）
  ↓
用户聊天
  ↓
助手思考态可视化（整理话语 → 检索记忆 → 应用 Persona 规则）
  ↓
系统后台提取记忆（长期 / 事件 / 目标）【Phase 2 落地】
  ↓
关系状态更新（信赖度↑、状态变化、陪伴时长累计）【Phase 2 落地】
  ↓
右侧 Memory 面板实时反映变化
  ↓
下一次见面体现（问候更贴合、记忆被引用）
```

关键体验节点：
- **首屏即存在**：无需说话，左栏已展示助手"在场"。
- **思考可见**：中栏 RAG 检索过程外显，展示技术能力。
- **记忆生长可见**：每次对话后右栏有变化，强化长期价值。

---

## 3. 页面结构

### 3.1 主页面（三栏 + Header）— 默认视图

```
┌──────────────────────────────────────────────────────┐
│                   Header（助手 · 称号 · 在线）          │
├─────────────┬──────────────────────────┬──────────────┤
│ 左：角色状态  │      中：聊天区            │  右：记忆档案  │
│ Persona      │      Chat                 │  Memory      │
│ Status       │                           │              │
│              │   [思考态检索指示]          │  长期记忆     │
│ 当前状态      │                           │  近期事件     │
│ 信赖度        │   [气泡]                  │  重要目标     │
│ 陪伴时长      │                           │              │
│ 最近记忆      │   [输入栏]                │              │
└─────────────┴──────────────────────────┴──────────────┘
```

- 默认窗口 **1180 × 760**，最小 **960 × 640**（与确认一致，固定最小宽度）。
- 三栏比例：**260（固定） : 1fr（弹性） : 300（固定）**。
- 左/右栏可在窄屏（<960）出现横向滚动条而非折叠（按"固定最小宽度"决策，不做响应式降级）。
- 栏间用 1px `BORDER` 分隔线，不用阴影做隔断，保持几何极简。

### 3.2 设置页（Settings）
- API Key 配置、模型选择、清空对话、关于。
- 入口：Header 右侧齿轮图标 → 模态视图（不跳出主页面上下文）。

### 3.3 Memory 页（记忆详情）
- 右栏 Memory 面板的展开版：按层（Short / Episodic / Semantic / Emotional / Relationship）浏览、搜索、删除记忆。
- **Phase 2 记忆层落地后启用**；v2 仅预留入口（或隐藏）。

### 3.4 Persona 页（人格详情）
- 现有 Persona 抽屉的扩展：完整 identity / speech 设定、行为标签、关系设定。
- 支持未来人格微调（不破坏稳定核心）。v2 保留现有右侧抽屉形态，三栏布局下改为点击 Header 人名展开。

---

## 4. 栏位详细设计

### 4.1 左栏：角色状态（核心，Presence 主战场）

组件 `PersonaStatusPanel`（新建 `ui/components/persona_status.py`）：

| 区块 | 内容 | 数据来源（v2 / 后续） |
|---|---|---|
| 头像+名称+称号 | AvatarProvider 提供头像；名称「助手」；称号来自 identity.title | identity.yaml（已有） |
| 当前状态 | emoji + 文案：🌱 平静 / 🤔 思考 / 😟 担忧 / 🙂 愉悦 | v2：轻量规则启发式（见 §5.5）；Emotion 模块落地后替换 |
| 信赖度 | 进度条 ███████░░（0–100，对应 trust_level），hover 显示数值 | v2：占位/本地持久化初值；Phase 2 接入 PersonaRuntime |
| 今日陪伴 | 已经陪伴 X 分钟（会话计时累计，进程级 + 本地累计） | 运行时计时 |
| 最近记忆 | 3 条最新事件 bullet（来自 Memory 层 recent events） | v2：占位/导入旧数据；Phase 2 接真数据 |

> 设计原则：左栏是"助手在场"的第一证据。状态文案用陪伴语气（如"今天也辛苦了，用户"），而非诊断式标签。

### 4.2 中栏：聊天区

- 保留 v1.5 的气泡、空态、错误态、滚动行为（向上滚动暂停回底 + 浮动新消息按钮）。
- **新增思考态 `ThinkingOverlay`**（新建 `ui/components/thinking_overlay.py`）：
  - 文案：「助手正在整理你的话……」
  - 检索进度清单（RAG 可视化）：
    - ✓ 用户长期记忆
    - ✓ 最近事件
    - ✓ Persona 规则
  - 实现：`core/agent.py` 在流式前发出阶段事件（`retrieving` → `reasoning` → `responding`），UI 订阅渲染。RAG 未接入前，由 Agent 发出**模拟阶段序列**（带短暂延迟），保证视觉完整；Phase 2 接真检索后自然替换。
- 输入栏保留，发送即触发思考态。

### 4.3 右栏：记忆档案（MemoryPanel，最大亮点）

组件 `MemoryPanel`（新建 `ui/components/memory_panel.py`），三个分区：

| 分区 | 内容形态 | 示例 |
|---|---|---|
| 长期记忆 | 标签 + 置信星标（★★★★★） | ★★★★★ 喜欢项目驱动学习 |
| 近期事件 | 时间线（日期 + 事件） | 8月5日 完成助手架构重构 |
| 重要目标 | 列表 | 完成长期陪伴 AI Agent |

- 来源：Memory 层数据（Phase 2）。**v2 用占位/导入数据，结构先行**。
- 交互：条目 hover 高亮；点击展开详情（后续 Memory 页承接）。

### 4.4 舰桥模式（Bridge Mode）— 默认全站基调

按确认结论，v2 **默认即本地语境**，不加切换开关：
- 视觉：简洁、紫白主色、少量动态元素。
- 不做游戏化，只营造"场所感"。
- 建议动态元素（克制）：
  - 背景极淡光晕呼吸（8s 循环，透明度极低）。
  - 左栏状态点脉冲（思考态时）。
  - 新消息气泡轻微上浮入场。
- 可用一条极细的青/金分隔线暗示本地标识感，但必须克制，不破坏极简。

---

## 5. Design System（扩展 `ui/theme.py`）

现有 `theme.py` 已建立 Colors / Typography / Spacing / Radius / Layout / Animations 六大 Token，且禁止硬编码。**v2 在其上扩展**，新增 Emotion / Trust / Bridge 相关 Token，不另起体系。

### 5.1 Colors（沿用并增补）
- 主色：`PRIMARY #7C6BC4`、`PRIMARY_LIGHT #EDE9FA`、`PRIMARY_DARK #5E4FA3`、`PRIMARY_SOFT #F7F5FD`、`ON_PRIMARY #FFFFFF`
- 背景/表面：`BG #FAFAFC`、`SURFACE #FFFFFF`、`SURFACE_SECONDARY #F2F2F6`
- 描边：`BORDER #E8E8EF`、`BORDER_STRONG #D8D8E3`
- 文字：`TEXT_PRIMARY #1F1F2E`、`TEXT_SECONDARY #6B6B7B`、`TEXT_MUTED #9CA3AF`
- **状态色（新增）**：
  - `STATE_CALM #8FBF9F`（平静·绿）
  - `STATE_THINKING #7C6BC4`（思考·紫，复用主色）
  - `STATE_WORRIED #E0A458`（担忧·琥珀）
  - `STATE_HAPPY #E58FA8`（愉悦·粉）
- **信赖度（新增）**：`TRUST_LOW #C9C3E0` → `TRUST_HIGH #7C6BC4`（渐变两端）
- **舰桥强调（新增，克制）**：`BRIDGE_ACCENT #7FB8C4`（极淡青，仅分隔线/光晕）

### 5.2 Typography（沿用）
- DISPLAY 24 / HEADLINE 18 / TITLE 16 / BODY 14 / CAPTION 12 / TINY 11
- 状态文案用 `CAPTION` + `WEIGHT_500`；信赖度数值用 `TINY`。

### 5.3 Spacing & Radius（沿用）
- 4/8/12/16/24 网格；三栏间距 `LG(16)`，栏内区块间距 `MD(12)`。
- 圆角复用 `SM/MD/LG/FULL`。

### 5.4 Animation（扩展）
- 沿用 `FAST 150 / NORMAL 250 / SLOW 300` + `EASE_OUT / EASE_IN_OUT`。
- **新增**：
  - `STAGGER: int = 80`（三栏入场依次延迟）
  - `PULSE_MS: int = 1200`（状态点脉冲周期，仅作为文档约定；脉冲通过 animate_opacity 过渡实现）
  - `BREATH_MS: int = 8000`（背景光晕呼吸周期）

### 5.5 Emotion States（新增模块）
新增 `ui/theme.py` 中的 `EmotionState` 与 `EMOTIONS` 映射（供左栏状态 + 头像切换）：

```python
@dataclass(frozen=True)
class EmotionState:
    key: str
    emoji: str
    label: str
    color: str

EMOTIONS = {
    "calm": EmotionState("calm", "🌱", "平静", c.STATE_CALM),
    "thinking": EmotionState("thinking", "🤔", "思考", c.STATE_THINKING),
    "worried": EmotionState("worried", "😟", "担忧", c.STATE_WORRIED),
    "happy": EmotionState("happy", "🙂", "愉悦", c.STATE_HAPPY),
}
```

- v2 状态由轻量规则启发式决定（如：收到消息→THINKING；回复中含关心词→WORRIED/HAPPY；空闲→CALM）。
- Emotion 模块落地后，由模块输出状态，UI 只消费。

---

## 6. Avatar Provider（解耦头像资源）

**问题**：v1.5 用文字"阿/博"规避版权，但长期目标下头像应随情绪变化，写死文字不可扩展。

**方案**：引入 `AvatarProvider` 抽象，UI 不直接构造头像。

```
resources/
 └── avatar/
      ├── default.png
      ├── happy.png
      ├── thinking.png
      └── worried.png
```

- 接口：`AvatarProvider.get(state_key, radius, text_size, **kw) -> ft.Control`
  - 返回 `ft.CircleAvatar`（文字或 `ft.Image`）。
- 默认实现 `TextAvatarProvider("阿")`（`v1.5` 行为，零素材依赖）。
- 未来 `ImageAvatarProvider`（读 `resources/avatar/*.png`，按 emotion 状态键映射）。
- `ui/components/avatar.py` 的 `make_avatar` 改为接收 `provider` 参数，所有组件经它取头像。
- Emotion 模块落地后，状态变化自动切换 `provider.get(state)`，用户"看见"情绪系统。

> v2 仍用 `TextAvatarProvider`，但**接口已就位**；后续放入 `resources/avatar/` 图片即可启用图像头像，无需改 UI 代码。

---

## 7. 文件改动清单（实现阶段）

| 动作 | 文件 | 说明 |
|---|---|---|
| 新建 | `UI_DESIGN.md` | 本文档落地 |
| 新建 | `ui/design/avatar_provider.py` | AvatarProvider 抽象 + TextAvatarProvider 默认实现 |
| 新建 | `ui/components/persona_status.py` | 左栏角色状态面板 |
| 新建 | `ui/components/memory_panel.py` | 右栏记忆档案面板 |
| 新建 | `ui/components/thinking_overlay.py` | 中栏思考态 + RAG 检索指示 |
| 改 | `ui/components/avatar.py` | `make_avatar(provider, ...)` 接 AvatarProvider |
| 改 | `ui/components/header.py` | 容纳三栏、齿轮入口、人名点击展开 Persona |
| 改 | `ui/components/chat_bubble.py` / `thinking_indicator.py` / `empty_state.py` / `persona_drawer.py` | 改调新的 make_avatar |
| 改 | `ui/app.py` | 三栏布局装配、订阅 Agent 阶段事件、舰桥基调背景 |
| 改 | `core/agent.py` | 发出 `retrieving/reasoning/responding` 阶段事件（RAG 未接入前模拟序列） |
| 扩 | `ui/theme.py` | 增补状态色、信赖度色、舰桥强调色、EmotionState、动画常量 |
| 新建（可选） | `resources/avatar/` | 占位目录（默认仍用文字头像） |

---

## 8. 实现顺序建议（Phase UI-Design → Build）

1. 写 `UI_DESIGN.md` 并确认（计划模式）。
2. 扩展 `ui/theme.py`（emotion / 信赖度 / 舰桥 / 动画 Token）。
3. 实现 `AvatarProvider` + 改造 `make_avatar`。
4. 左栏 `PersonaStatusPanel`（先用占位状态/时长/记忆，结构先行）。
5. 右栏 `MemoryPanel`（占位数据，结构先行）。
6. 中栏 `ThinkingOverlay` + `core/agent.py` 阶段事件（RAG 未接入前模拟序列）。
7. 三栏装配 + 舰桥基调背景 + 入场/脉冲/呼吸动效。
8. 接真实数据（Phase 2 记忆层、Emotion 模块、Agent 真检索）——后续 Phase，不在 v2 范围。

---

## 9. 待确认 / 风险

- **状态来源**：v2 左栏状态/信赖度/陪伴时长是运行时占位，需本地持久化（信任度、累计时长）以免重启归零——建议放 Phase 2 数据层，v2 先用内存 + 可选本地 JSON。
- **RAG 检索指示**：v2 为模拟阶段序列（视觉完整），非真实检索；需在 `agent.py` 预留事件接口，避免 Phase 2 返工。
- **Memory 面板数据**：v2 占位/导入，真实内容依赖 Phase 2；面板交互（hover/展开）先行。
- **窄屏**：按确认不降级，固定最小宽度 960×640。
- **版权**：默认文字头像，图像头像待用户后续提供 `resources/avatar/` 素材，不内置受版权素材。
