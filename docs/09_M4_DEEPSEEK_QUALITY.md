# M4 真实 DeepSeek 抽取与质量验证（分三阶段）

> 设计基线：M4 不追求「自动记忆」能力堆叠，而追求「不会污染人格与用户记忆的长期陪伴 Agent」。
> 分阶段推进：**M4.1 接入 → M4.2 真实质量验证 → M4.3 优化**。

## M4.1 DeepSeek 接入（最小实现）✅

- 目标：让 `ExtractionEngine` 在真实 DeepSeek 下可用。
- 范围：
  - `core/llm_client.py` 的 `DeepSeekLLMClient` 补齐 `chat()`（非流式单发，`stream=False`，
    解析 `choices[0].message.content`；非 200 / 结构异常抛 `RuntimeError`，由上游静默降级为 `[]`）。
  - `ui/app.py` 已在 Composition Root 构造 `DeepSeekLLMClient` 并注入 `ExtractionEngine`（既有）。
  - `config.py` 已读 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` / `EXTRACT_AUTO`（既有）。
- **不做**：自动后台抽取、自动确认、自动修改 Memory。
- 守护：`EXTRACT_AUTO` 默认 `False`，抽取只走手动「整理记忆」按钮（人触发 → AI 只提议 candidate）。

## M4.2 真实抽取质量测试（最重要）✅

落到 `tests/test_extraction_quality.py`，直接打真实 DeepSeek（`DEEPSEEK_API_KEY` + 网络，无 key 自动 skip）。
验收用例：

| Case | 输入 | 期待 |
|------|------|------|
| 1 应记 | 「我叫小明，今年大三，正在准备 AI Agent 方向实习。」 | fact 含『大三/学生』；goal 含『AI Agent/实习』 |
| 2 不记 | 「今天下雨了，好烦。」 | 不产生任何候选 |
| 3 情绪非事实 | 「最近压力特别大。」 | 禁止固化成『长期/性格焦虑』类 fact；允许 event 或不记 |
| 4 角色隔离 | 「助手喜欢胡萝卜。」 | 胡萝卜相关候选必须指向助手，绝不张冠李戴成用户偏好 |

**结构不变式（离线可跑）**：`test_gate_offline_never_writes_long_term` —— 无论 LLM 产出什么，
`ExtractionEngine` 永远只写 `memory_candidate` 闸门，绝不直写 long_term 正表（人工未确认前零污染）。

运行：
- `python -m pytest tests/test_extraction_quality.py -v`
- 或直跑报告：`python tests/test_extraction_quality.py`

## M4.3 优化（后续，未启动）

仅当 M4.2 暴露质量缺口时再做：
- Prompt 优化（抽取系统提示词）
- JSON schema 强约束（如 response_format / 更严的 parse）
- confidence 校准
- memory type 分类微调

> 关于 M3 的两处 minor（MemoryPanel 430 行、edit dialog 重复）：按架构建设期策略，**暂不修**，
> 待 M4.2 确认结构正确、进入「优化阶段」再处理。
