# 审计问题修复报告（按总体设计验收地图）

> 日期：2026-08-17
> 依据：docs/12_TOTAL_DESIGN_ACCEPTANCE_MAP.md「需要提前修的架构问题」清单
> 基线 commit：`78296cb`（修复前，可直接 `git reset --hard 78296cb` 回滚）
> 验证：**全量 225 通过 / 5 跳过**（修复前 209 通过；新增 16 个回归测试，0 回归）

---

## 修复清单（对照 docs/12 待修列表）

### 1. TTS 状态外显 ✅（原缺口：§5.4 / §16）

- 新组件 `ui/components/tts_status.py`：
  - `TTSStatusState`：合成/播放忙碌的可观察状态；
  - `TTSStatusBanner`：「语音服务暂不可用」提示条（可点 × 关闭）。
- 🔊 按钮订阅忙碌态：合成/播放中**禁用 + 沙漏图标**（ChatBubble 新增 `tts_state` 参数）。
  副收益：连点多个气泡的**并发合成收敛为串行**（忙碌期间所有 🔊 禁用）。
- `_speak` 全程忙碌态包裹：合成 → 排队 → `wait_all()` 等播完 → 解除；
  失败亮横幅「语音服务暂不可用，这条不朗读；文字聊天不受影响」，成功撤横幅。
- 启动时后台探活（`health_check`）：TTS 未启动则横幅提示
  「语音服务未启动……需要语音时请先运行 start_assistant.bat」。

### 2. 停止生成 ✅（原缺口：§5.2）

- `Agent.request_stop()`：流式回复可中途停止，**已生成的部分照常落库、入短期窗口、
  触发完成事件**（不丢内容）；停止标志消费后自动复位，下一轮不受影响。
- `InputBar` 新增 `on_stop`：生成中发送键变红色**停止键**（STOP_CIRCLE），
  点击即停；未注入 `on_stop` 时保持旧转圈禁用行为（兼容旧调用方）。

### 3. LLM 退避重试 ✅（原缺口：§2.1）

`DeepSeekLLMClient`（协议不变）：
- 流式：连接建立失败 / 超时（ConnectionError/Timeout）且**尚未产出任何内容**时
  退避重试 2 次（1s/2s）；已产出内容绝不重试（会重复文本）；耗尽后上抛交上层降级。
- 非流式 `chat()`：连接类异常同样重试（幂等）。非 200 业务错误不重试。

### 4. 「新对话」UI 入口 ✅（原缺口：§2.3 / §5.2）

- Header 新增按钮（ADD_COMMENT 图标）：轮换 session（`Agent.reset()`，不删库）+
  清空当前视图。当天已聊内容仍归档在「今天」，可从日期导航回看（与时间轴语义一致）。

### 5. voice / lang 入配置 ✅（原风险：§7 / §14）

- `config.py` 新增 `TTS_VOICE`（默认 assistant）/ `TTS_TEXT_LANG`（默认 zh），
  `.env.example` 同步补注释。`_speak` 不再硬编码——换声音只改 .env，不动代码。

### 6. on_reply_complete 事件钩子 ✅（原状态：仅设计）

- `Agent` 新增可选参数：回复完成（含停止后的部分回复）时回调，**只通知不管 TTS**
  （边界按总设计 §2.4 冻结）。回调异常只记日志。可用于未来日志/摘要/插件。

### 7. 播放停止 ✅（原缺口：§10）

- `AudioPlayer.stop_all()`：清空待播队列 + `PlaySound(None,0)` 打断当前播放；
  `wait_all()`：阻塞至队列播完（后台线程用）。
- 接线：**按下静音即刻停播**（`MuteState` 订阅 → `stop_all`）——符合「静音=世界安静」预期。

### 附带修正

- `ChatArea` 退订逻辑泛化：`clear()` 现同时退订 MuteState 与 TTSStatusState 两类订阅
  （沿用防泄漏模式）。
- `tests/test_m3_tts_ui.py` 的 `_FakePlayer` 替身补 `wait_all()`（新行为配套）。

## 明确不做的（及原因）

- **拆分 ui/app.py 组合根**（待修 #1）：属结构性重构而非缺陷修复，与「不要改架构」
  约束冲突；本轮已把新增接线控制住，拆分留待下一个功能动工时一并做。
- **设置页 / Markdown 渲染**：V1.5+ 范围，审计中本就未列入待修。
- **TTS「播放中」独立状态图标**：已由忙碌态覆盖（沙漏持续到播完），足够 v1。

## 验证记录

1. `pytest tests/ -q` → **225 passed, 5 skipped**（跳过 = 需真实 TTS 服务的集成用例）
   - 修复前基线 209 通过；新增 `tests/test_audit_fixes.py` 16 例全部通过；
   - 全量首轮曾因旧 `_FakePlayer` 缺 `wait_all` 挂 2 例，补替身后全绿（非产品代码回归）。
2. 新增覆盖：LLM 重试三分支（重试成功/已产出不重试/耗尽上抛）+ 非流式重试、
   停止后部分回复落库+事件、stop_all 清队列停播、忙碌态订阅/退订、横幅显隐、
   🔊 忙碌图标、停止键切换、新对话按钮、TTS_VOICE 环境变量、_speak 忙碌包裹与
   voice 配置透传、失败亮横幅。
3. `import ui.app / config` 通过；`git status` 干净。

## 回滚

```bash
git reset --hard 78296cb   # 回到本次修复前
```

单文件回滚：`git checkout 78296cb -- <路径>`。
