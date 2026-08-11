# Phase 3：Persona Engine 设计（已批准基线）

> 本文档是 Phase 3 的**已批准设计基线**，在 `persona.py → persona/` 迁移与实现前提交，
> 作为后续改动的回滚点。所有实现均按本文件落地，如需变更须先更新本文件并重新确认。

## 0. 目标

把 Agent 从「有记忆的聊天机器人」升级为「具有助手人格的陪伴 Agent」。
本轮**只实现 Persona Engine**；Emotion / Voice 仅保留 seam，不创建完整逻辑。

## 1. 已批准的 5 项设计调整

1. **`worldview` 不作为 Persona 字段名**。拆分：
   - Knowledge 保存**客观世界事实**（`knowledge/*.md`）。
   - Persona 保存**角色价值观 / 认知方式**。
   - `worldview` 改名为 `perspective`（认知视角）。
2. **Relationship 独立存储**：`persona_state` 表不进入 Memory；Memory 只表示**用户事实与经历**；
   Relationship 管理 `trust` / `stage` 等**系统状态**（已存在于 `store.py` 的 `persona_state` 表）。
3. **PromptBuilder 注入顺序（人格先于上下文，减少角色漂移）**：
   1. 身份锚定
   2. → Persona 核心价值观（perspective + values + thinking_style）
   3. → Knowledge
   4. → Memory
   5. → Relationship
   6. → Emotion seam（暂留，不接入真实模型）
   7. → Behavior rules
   8. → Speech style
   9. → Shield
4. **Stage v1 不使用「家人」**，采用四档：**初识 / 熟悉 / 信任 / 深度陪伴**。
5. **本轮只实现 Persona Engine**；Emotion（`emotion_block` seam）与 Voice（不建）只保留接口位。

## 2. 边界约定

- **Persona ↔ Knowledge**：Knowledge = 客观设定/世界观资料（RAG 只读）；Persona = 助手的「表现方式」（身份、价值观、认知、语言风格、行为准则、关系状态）。可检索的客观事实归 Knowledge；主观立场/口吻归 Persona。
- **Persona ↔ Memory**：Memory = **关于用户（用户）的经历**（fact/preference/event/goal/relationship 用户侧）。Persona = **关于助手自身**的固定特质 + 对用户的动态关系状态（`persona_state`）。
- **Relationship 不进 Memory 表**：动态状态（trust、companionship、stage）存 `persona_state`，由 `RelationshipManager` 通过 `MemoryManager.state()/save_state()` 读写；`memory` 表的 `relationship` 类型仍保留给「用户主动披露的关系类事实」（检索语义不同，注释区分）。

## 3. 文件结构

```
core/persona/
  __init__.py          # 兼容旧 import：Persona / load_persona / PersonaManager
  persona.py           # Persona dataclass + load_persona()（由 core/persona.py 迁入，worldview->perspective）
  loader.py            # YAML 加载：load_identity/load_speech/load_behavior/load_relationship
  behavior_rules.py    # BehaviorRules：behavior.yaml -> 【行为准则】
  relationship.py      # RelationshipManager：persona_state 读写 + stage 计算 + 【与用户的关系】block
  persona_manager.py   # PersonaManager 门面：behavior_block() / relationship_block() / values_block()
config/persona/
  identity.yaml        # 固定身份（name/title/species/perspective/values/thinking_style/knowledge_scope）
  speech.yaml          # 语言风格 + few-shot（不变）
  behavior.yaml        # 新增：behavior_guidelines
  relationship.yaml    # 新增：stages（初识/熟悉/信任/深度陪伴）+ 各阶段背景
```

Emotion / Voice seam（本轮不实现）：
```
core/prompt_builder.py  # emotion_block 关键字参数保留为 seam
```

## 4. PromptBuilder 注入顺序（最终）

```
[1 身份锚定]
[2 【助手的视角与价值观】 perspective + values + thinking_style]   # 人格定调，先于上下文
[3 【角色知识】 knowledge_block]                                      # Knowledge RAG（动态检索）
[4 【相关用户记忆】 memory_block]                                    # Memory（Top-K 动态检索）
[5 【与用户的关系】 relationship_block]                              # Relationship（动态 persona_state，仅背景事实）
[6 【当前情绪】 emotion_block]                                      # Emotion seam（None）
[7 【行为准则与边界】 behavior_block]                                # BehaviorRules + knowledge_scope
[8 【语言风格】 + few-shot]                                          # 固定
[9 稳定性护盾]
```

## 5. 存储归属

| 数据 | 表 | 管理者 |
|------|----|--------|
| 用户事实/经历 | `memory` | MemoryManager |
| 对话流水 | `conversation` | MemoryManager |
| 关系/信赖系统状态 | `persona_state` | RelationshipManager（经 MemoryManager） |
| 角色设定/语言 | `config/persona/*` YAML | PersonaManager（只读） |

## 6. 测试计划

- 旧 import 兼容：`from core.persona import Persona / load_persona / PersonaManager` 均可用。
- `worldview` 改名：`load_persona().identity` 含 `perspective`、不含 `worldview`。
- Stage 映射：trust 阈值 → 初识(0)/熟悉(30)/信任(60)/深度陪伴(85)。
- 注入顺序：identity < 价值观 < 知识 < 记忆 < 关系 < 情绪 < 行为 < 语言。
- 三柱隔离回归：Knowledge 不污染 Memory、Memory 不污染 Knowledge、无关记忆不强制注入。

## 7. 不在本轮范围

- Emotion 真实模型、Voice 真实模型。
- Relationship 的 trust 自动涨跌逻辑（仅提供 set_trust/bump 方法与 stage 计算，调用留待后续）。
- persona_status.py 左栏信赖度接真实数据源（当前为占位，本轮不改）。
