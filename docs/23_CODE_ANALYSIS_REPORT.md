# 深度代码分析报告（consultant 视角，Amiya-Agent）

> 日期：2026-09-06
> 性质：先行分析（本轮 CRITICAL 两项已修复并有测试，其余为路线图输入）
> 前置：[22_P3C_QUALITY_REPORT.md](22_P3C_QUALITY_REPORT.md)（规范符合度视角）
> 分析框架：P1 正确性至上 · P2 可诊断性 · P3 非法状态不可表示 · P4 单一职责 ·
> P5 显式优于隐式 · P6 最小表面 · P7 用测试证明 · P8 安全演进 · P9 故障 containment · P10 注释讲为什么

---

## Executive Summary

核心层（memory store / 检索 / 抽取闸门 / TTS service）对一个个人规模的项目而言
纪律性罕见地好——分层由构造强制而非约定，降级系统化，测试覆盖了"停止生成时半截
回复落库"这类微妙行为。风险集中在边缘：`ui/app.py` 是 840 行的组合根，曾有**三套
互相矛盾的线程策略并存**；运行时换肤链路读取过期环境变量（真实用户可见 bug，已修）；
被文档与测试围绕的主动问候子系统是**从未被调用的死代码**。

## Health Scores

| 维度 | 评分 | 一句话 |
|---|---|---|
| Correctness Risk | 6→7.5/10 | 换肤过期 env、生成期丢半截回复两 bug 已修；`history_limit=0` 切片陷阱已堵 |
| Type Safety | 6.5/10 | Protocol + runtime_checkable 用得好；dict 形状数据遍地、.env/YAML 数值解析裸奔（已兜底关键路径） |
| Observability | 7→8/10 | 几乎每个 except 都带上下文日志（本轮再补 12 处静默点）；`memory_degraded` 仍无 UI 出口 |
| Test Coverage | 7.5/10 | 核心深（重试/停止/闸门/存储）；proactive.py 零测试、真实重建路径被 mock 掉 |
| Maintainability | 6.5/10 | 组合根过载；values_block/皮肤选择 UI/编辑对话框重复；死代码三处 |

## 本轮已修复（原 CRITICAL / HIGH）

1. ✅ **[Threading/Lifecycle] 换肤过期环境变量**（原 app.py:722 + config.py:97）
   —— `persist_ui_skin/layout` 现同步 `os.environ`；测试守护。细节见 docs/22 §3.1 C1。
2. ✅ **[Threading/Lifecycle] 生成期导航丢半截回复**（原 app.py:269/333/789 +
   agent.py:274）—— `_generating` 单一 busy 源 + 导航防护 + `_drain_after_view_lost`
   优雅收尾；7 测试守护。细节见 docs/22 §3.1 C2。
3. ✅ **[Fault containment] relationship.yaml 阈值炸穿 reply** —— `_threshold_of` 兜底。
4. ✅ **[Resilience] llm_client 重试缺口** —— `_TRANSIENT` 补 `ChunkedEncodingError`。
5. ✅ **[Resilience] 连接/wave 资源未显式关闭**（4 处）—— 统一 `with`。
6. ✅ **[Concurrent] embedder 懒加载双检锁**。
7. ✅ **[Data-loss] `forget("%")` 通配符清库** —— LIKE ESCAPE。
8. ✅ **[Boundary] `history_limit=0` 全量历史陷阱** —— 构造期校验。

## 遗留发现（按优先级）

### HIGH

**[Observability] `core/agent.py:98,122,138,346`** — `memory_degraded` 每次记忆
失败都置位（注释写"UI 可据此提示"），但 `ui/` 无任何读取点。DB 损坏的用户会
无感知地聊天，且不知道什么都没被记住——这恰是产品哲学里"记错比忘记更伤"的
反面。**建议**：复用 `TTSStatusBanner` 的 subscribe/notify 模式做持久横幅。

**[Resilience/Transactions] `ui/app.py:749-760`** — 换肤重建非事务：`page.clean()`
发生在 `_build_ui` 之前，中途失败（皮肤资源缺失/Flet API 变动）只留一屏空白，
"重建失败绝不拖垮会话"的注释与事实不符。**建议**：先在局部变量搭好新控件树，
`clean()` 与 `add()` 背靠背执行；失败则回滚旧皮肤上下文。（防线 1 已能挡住
生成期重建，此项针对空闲期重建自身的健壮性。）

**[P6 死代码] `core/proactive.py`、`Agent.proactive_greeting`、
`RelationshipManager.bump_companionship`** — 实现完整、测试齐全、调用点为零
（app.py:7 明言"启动不自动发送问候"；`companionship_seconds` 从未被生产写入，
状态栏用的是 `time.monotonic()`）。docs/19 遗留 #1 的"收编或放弃"悬而未决。
**建议**：产品决策后二选一；删除时连测试一起（ROADMAP 记录）。

### MEDIUM

- **[P4 组合根] `ui/app.py`**（run 137 行 + ~25 个可变字段）：换肤重建必须手工
  重放 run() 的构造顺序（`_setup_page → _build_ui → _init_history_ui`），第二条
  分叉的构造序列已酿过窗口关闭 bug（L698-704 有实测记录）。**建议**：抽出
  `build_agent_stack(settings) -> AppContext` 与 `build_ui(ctx)`，重建 = 重跑一个
  函数——这是全仓库杠杆最高的重构（docs/19 架构待办 #5 同源）。
- **[P3 边界] `config.py:71-74`**：`TEMPERATURE=0,9` 之类会在启动时裸抛 ValueError
  而非 `ConfigError`，UI 走不到 `_fatal` 设计路径。**建议**：`_env_float/_env_int`
  助手统一抛 ConfigError。
- **[P5 TOCTOU] `input_bar._is_loading` 跨对象探测**已随本轮改读 `_generating`
  消除；同模式 `chat_area._mute_cb` 探测建议组件暴露只读属性。
- **[P2 静默失败] `memory_panel.py:551-556`**：run_task 失败退化分支会在抽取
  worker 线程直接 `refresh()`（重建控件树）。低概率，建议退化分支只置脏标记。
- **[重复] `values_block`**（prompt_builder.py:146 ≡ persona_manager.py:60，后者
  无生产调用方）、**皮肤选择 UI 两套**（persona_status 220-325 ≡ persona_drawer
  144-248）、**编辑对话框三份**（memory_panel 407/468/565）——三处都到了"改一
  必漏二"的临界点。

### LOW

- `.env` 写入非原子（写临时文件 + `os.replace` 一行级改动）。
- `store.py` `load_state` 缺行防护、LIKE 转义 —— ✅ 本轮已修，此处仅存档。
- `app.py:144-145` / `skin.py:206-207` 引用不存在的 `last_emotion` persona_state
  键（真实键为 `emotion`）——陈旧注释（P10）。
- `thinking_indicator.py`（26 行）+ 导出被 overlay 取代，仅自测试引用——可删。
- `EXTRACT_AUTO=True` 时抽取同步挂在 reply 尾部（UI 忙碌态被拉长一个完整 LLM
  往返）——默认 False 暂无影响，开启前需异步化。
- `agent.py:172` `window_turns or 20`：显式传 0 会被吞——应 `is None` 判断。
- `date_nav.py:204` and/or 链在 falsy 值时静默转 None——显式三元。
- `memory_panel.py:598` 手动记忆默认权重 5/4、`app.py:534` 轮询 30s、
  `app.py:295` keep_months=3 —— 产品参数宜入 .env。

## Quick Wins（高收益低成本，多数本轮已做）

| # | 项 | 状态 |
|---|---|---|
| 1 | 换肤/栏宽 persist 同步 os.environ（一行级，救活头牌功能） | ✅ 已修 |
| 2 | 生成期禁 新对话/日期导航（单一 `_generating` flag） | ✅ 已修 |
| 3 | 视图丢失排空生成器保住落库 | ✅ 已修 |
| 4 | `history_limit≥1` 校验 + .env 数值转换包成 ConfigError | ✅ / ☐ 前半 |
| 5 | `.env` 原子写 | ☐ |
| 6 | 删 `thinking_indicator.py`；proactive 三件套裁决 | ☐ 需决策 |
| 7 | `memory_degraded` 横幅外显 | ☐ |
| 8 | LIKE 转义 / 阈值兜底 / 双检锁 / 资源 with | ✅ 已修 |

## Strengths（值得保持的模式）

- **分层由构造强制**：`MemoryExtractor` 只拿到 `MemoryManager.propose` 一条写路，
  自动抽取**结构上不可能**绕过人工确认闸门——全仓库最好的 P3 示范。
- **带原因的降级而非沉默**：TTS 失败返回带类型的 `TTSResult`；记忆/知识/嵌入失败
  记日志后继续——聊天可用性始终优先于功能完整性。
- **向量模型标记**：v3 迁移给每行向量打 `embedding_model/dim` 戳，跨模型向量
  不可能伪造余弦相似度；迁移链每步带 `user_version` 提交点。
- **从真实 bug 沉淀的注释**：`embedder.py:92-102` 解释为何用 blake2b 而非 hash()
  （PYTHONHASHSEED 会静默毁掉跨重启检索）——P10 的教科书写法。
- **正确的流式语义**：`stream_chat` 只在首个 token 前重试 + "已产出"守卫；
  `request_stop` 的半截落库有测试。
- **纯函数缝**：`proactive.py`/`emotion.py`/`layout_metrics.py`/`chunk_text` 无副作用
  可独立测试；`skin.resolve()` 注入 `now` 保证确定性。
- **可观察状态对象**：`MuteState`/`TTSStatusState` 快照式通知 + 气泡移除时显式
  退订——避开了 Flet 经典订阅泄漏。

## Technical Debt Inventory

| 项 | 规模 | 备注 |
|---|---|---|
| app.py 组合根拆分（AppContext + build_ui） | L | 防止第三条构造序列出现 |
| HealthBus + 线程规则成文 | M | docs/19 待办 #6；四种降级样式待统一 |
| TurnPipeline + metrics.jsonl | L | Tools 动工门槛（docs/19 待办 #7） |
| 死代码裁决（proactive ×3） | S | 产品决策 |
| 重复代码三组收敛 | M | values_block / 皮肤 UI / 编辑对话框 |
| `_worker` 线程策略成文或收口 | S | 二选一，写进模块 docstring |
