# Assistant Agent 总体设计与验收地图

> 日期：2026-08-16（时间轴功能落地后）
> 用途：把围绕助手 Agent 的完整产品设想收拢成一张总设计图，逐块反查仓库现状——
> **当初想做什么 / 哪些已实现 / 哪些只是设计 / 哪些被放弃 / 哪些架构要提前修**。
> 状态标记：✅ 已实现并验证 · 🟡 部分实现 · ⬜ 仅设计未动工 · ❌ 显式放弃
> 证据均为当前 HEAD（`798d227`）的实际代码，全量 209 测试通过 / 5 跳过。

---

## 全生命周期架构图（现状实录）

```
用户 ⇄ Flet UI (ui/app.py + components)
        │  文字                    │  run_thread
        ▼                          ▼
     聊天区/时间轴            Agent (core/agent.py)
     DateNav 日期导航           │  system 提示词（PromptBuilder：身份→时间→价值观
     🔊 朗读按钮               │   →知识→记忆→关系→情绪seam→行为→语言→护盾）
        │                       ├── LLM: DeepSeekLLMClient（流式 SSE，timeout 30s）
        │                       ├── MemoryManager → SQLiteStore（对话/长期记忆/候选/画像）
        │                       │    └─ Embedder(bge) 向量检索 Top-K
        │                       └── KnowledgeManager（knowledge/*.md RAG）
        │  点击 🔊（UI 层，Agent 不知道 TTS 存在）
        ▼
     TTSService (core/tts/service.py)  ← assistant.yaml voice profile
        │  HTTP /tts（分块→cut0→拼接淡变）
        ▼
     GPT-SoVITS api_v2 :9880（独立进程，e15+e8，start_assistant.bat 拉起）
        ▼
     AudioPlayer（唯一工作线程 FIFO，winsound 同步播放）
        ▼
      🔊
启动/关闭：start_assistant.bat（GBK 编码）→ 环境检查 → 复用或启动 TTS → health 等待(600s)
           → Agent 独立窗口；关 Agent 不杀 TTS
故障恢复：每层失败只降级不冒泡（LLM→错误气泡；TTS→跳过朗读；记忆→纯内存；知识→跳过）
```

---

## 1. 产品核心定位

| 要素 | 状态 | 说明 |
|------|------|------|
| LLM 大脑 | ✅ | DeepSeek 流式接入，Protocol 抽象可换模型 |
| 助手人格 | ✅ | 四柱 yaml + 注入顺序设计 + 稳定性护盾 |
| 本地工具能力 | ⬜ | **完全没有**。无 Tool Router、无任何工具（见 §4） |
| 记忆能力 | ✅ | 短期窗口 + SQLite 长期记忆 + 向量检索 + 候选闸门 |
| 本地语音 | ✅ | GPT-SoVITS 独立进程 + Voice Profile + winsound 播放 |
| 桌面 Agent 形态 | 🟡 | 目前是「桌面陪聊 + 语音」；「会做事」尚未开始 |

**结论：定位五要素里四项落地，唯独 Tools 零开工——这是与总设计图最大的差距。**

## 2. LLM / Agent 核心能力

- 2.1 基础对话 🟡
  输入✅ / 流式✅（SSE→delta→气泡逐字）/ 完整收集✅ / 错误处理✅（错误气泡+日志，不伪装台词）/ 超时✅（TIMEOUT=30s 可配）/ **重试⬜**（瞬时抖动直接降级为错误气泡，无退避重试）。
- 2.2 助手人格 ✅
  固定人格与说话风格（speech.yaml：tone/称呼/句尾/词汇/style_rules）、角色一致性（身份锚定最硬 + 人格先于上下文注入 + few-shot 范例）、不泄内部信息（稳定性护盾「不要输出元评论」）。动态关系背景（relationship stage）已有。
- 2.3 上下文 🟡
  当前会话✅（history_limit×2 滚动窗口）/ 历史对话恢复✅（跨会话回填最近 40 条）/ 裁剪✅（硬裁剪）/ **长上下文压缩⬜** / 新会话🟡（`Agent.reset()` 存在但 **UI 无入口**）/ 会话恢复✅。
- 2.4 on_reply_complete ⬜
  设计冻结为「回复完成事件，不管 TTS」；**代码中不存在此回调**。TTS 由 UI 层 🔊 直接触发，与冻结边界一致。留给未来的日志/摘要/插件钩子未开建。

## 3. Memory / 记忆系统

- 3.1 短期记忆 ✅ —— `_history` 窗口喂 LLM。
- 3.2 会话记忆 ✅ —— conversation 表带 session_id；本次时间轴按天浏览正建立在其上。
- 3.3 长期记忆 ✅ —— memory 表（类型/置信度/重要度/hits/衰减字段）+ bge 向量 + Top-K 检索注入 + LLM 抽取进候选闸门（人拍板后转正，EXTRACT_AUTO 默认关）。
- 3.4 记忆治理 🟡 —— 查看/确认/删除/黑名单/关键词清理/候选拍板均有（MemoryPanel 616 行 UI + memory_gate 测试）；「一键清空全部记忆」与隐私数据专项处理无。docs/03 的 G1（零真实数据）、G2（抽取未接线）、G8（无提交基线）三个历史缺口现已闭合。

## 4. Tool / Action 能力 ❌（整体未动工）

- 无浏览器/文件/GitHub/本地程序/系统命令/搜索/日历任何工具。
- 无 Tool Router 层。agent.py 现在是「提示词编排器」，不发起任何行动。
- **架构建议**：动工时按 `Agent → ToolRouter → Tools` 建，红线同 §14（Agent 不直接 import 具体工具）。这是 V1.5 的主战场，当前零负债（因为零代码），反而是最干净的一块。

## 5. UI / Flet

- 5.1 Chat 🟡：用户/助手气泡✅、流式✅、滚动✅（自动跟随+回底按钮）、长文本🟡（限宽换行，无折叠）；**Markdown/富文本⬜**（纯 Text；人格提示词禁止 Markdown 输出，算缓解）。
- 5.2 输入 🟡：发送✅；**停止⬜（流式卡住只能等超时）、重试⬜、清空/新对话⬜**（后端 `Agent.reset()`/`ChatArea.clear()` 都在，缺 UI 入口）。
- 5.3 设置 ⬜：无设置页（模型/温度/TTS 参数都靠 .env + yaml）；静音✅ 算唯一例外；**TTS 服务状态指示⬜**。
- 5.4 UI 状态 🟡：LLM 生成中✅（thinking overlay + header + 左栏三处联动）；**TTS 生成中⬜、播放中⬜、服务不可用⬜**（合成失败只进日志，用户无感知）。
- 时间轴（本次新增）✅：日期下拉/翻页/回到今天、当天自动回显、历史日只读。

## 6. TTS 模型资产 ✅

GPT `助手-e15.ckpt` + SoVITS `助手_e8_s680.pth`，实测中/英/混合后定稿 e15+e8；assistant.yaml 记录在案。注意：/tts 不发送模型字段，权重由 TTS 进程启动时从 tts_infer.yaml 加载——**换模型需重启 TTS 进程**（yaml 注释已声明 v1 不热切换）。

## 7. Voice Profile ✅

`core/tts/voice_profiles/assistant.yaml`（ref_audio/ref_text/prompt_lang/api 端点/timeout）；Agent 侧只传 `voice="assistant"`，不感知 .ckpt/.pth。目录式设计天然支持多声音扩展（`list_voices()` 已备）；**UI 声音切换入口⬜**。⚠️ 小瑕疵：voice 名硬编码在 `ui/app.py._speak`，换声音要动代码，宜移入 .env/config。

## 8. GPT-SoVITS 独立进程 ✅

HTTP :9880 松耦合、WebUI 不参与、API 常驻、崩溃不拖死 Agent、可独立重启、重复启动自动复用。**换模型🟡**（重启 + 改 tts_infer.yaml，无热切换接口调用）。

## 9. TTSService ✅

text→HTTP→audio 的薄封装；Voice Profile/请求构造/timeout/全类型异常→TTSResult 分类错误；不知道 UI/播放/Agent/GPT 内部。边界经 2026-08-16 专项审查验证干净（docs/11）。

## 10. 播放器 🟡

v1 范围：点击 🔊 播当前气泡✅、静音（全局开关+按钮自动隐藏）✅；**停止⬜**（无停止按钮，FIFO 队列无清空接口）。冻结不做的四项现状：自动朗读⬜ / 流式 TTS⬜ / 音频队列🟡（**边界微调**：修复版加了播放 FIFO 排队消除互相截断——这是「播放串行化」而非设计里冻结的「TTS 合成队列」，边生成边播仍未做）/ 多句自动拼接⬜。

## 11. 多语言 TTS ✅

text_lang 与 prompt_lang 分离（yaml + synthesize 参数），与官方 /tts 契约一致；中/英/混合有集成测试（t1–t3）。

## 12. 长文本 🟡（提前实现了一半）

设计冻结「v1 一条气泡=一次 TTS，v2 再分句队列」。实际：v1 已落地**客户端分块（≤50字 cut0）+ 拼接（裁静音/淡变/停顿）+ 空响应退避重试**——一次点击一次完整出声，用户无感；**v2 的边生成边播仍未做**。属「提前落地但未破坏一次点击一次出声的边界」，已标注。

## 13. 本地离线运行 ✅

start_assistant.bat 一键（检查→复用/启动 TTS→health 等待→Agent），用户无感。编码问题已实证修复（GBK 方案，docs/11 §0）。

## 14. 依赖方向红线 ✅（干净，一处待改进）

```
UI → Agent → TTSService → GPT-SoVITS API     实测单向 ✅
```

- Agent 不知 torch/GPT-SoVITS/9880/播放器/Flet ✅（core/agent.py 无任何 TTS import）。
- TTSService 不知 UI/消息/Agent ✅；Player 只认 wav 字节 ✅。
- 待改进：① voice 名与 text_lang 硬编码在 `ui/app.py._speak`（应入 config）；② **ui/app.py 的 run() 已膨胀成巨型 Composition Root**（记忆+知识+LLM+抽取+TTS+播放+时间轴全在一处构造），再加两三个功能就该拆 bootstrap 模块了。

## 15. 一键启动与生命周期 ✅

bat 三保护（复用/600s 超时/独立窗口）；关闭 Agent 不影响 TTS ✅；「可选关闭 TTS」无入口 🟡；服务常驻 ⬜。

## 16. 容错 / 降级 ✅（本项是全仓库最强的 invariant）

LLM 挂→错误气泡不崩 ✅ / TTS 挂→聊天正常 ✅ / 播放器挂→聊天继续 ✅ / TTS 超时→错误结果不冒泡 ✅ / 记忆挂→纯内存 ✅ / 知识挂→跳过 ✅ / 抽取挂→静默 ✅。
**唯一缺口🟡**：设计要求 TTS 不可用时提示「语音服务暂不可用」，实际只写日志，UI 无提示；「取消合成」无 UI。

## 17. 八大系统块总验收表

| 系统块 | 状态 | 主要缺口 |
|--------|------|----------|
| LLM | 🟡 | 无重试、无停止生成 |
| 助手人格 | ✅ | — |
| Memory | ✅ | 一键清空、隐私专项（治理其余齐备） |
| Tools | ❌ | 整块未开工 |
| Flet UI | 🟡 | 设置页、停止/重试/清空入口、Markdown、TTS 状态外显 |
| TTS / GPT-SoVITS | ✅ | 停止播放、声音切换入口 |
| Offline Runtime | ✅ | — |
| Error Handling | ✅ | TTS 故障的用户可见提示 |

## 18. 版本边界对照

**V1 必须**（LLM+人格+基础上下文+Flet+TTS+🔊+离线+基础容错）：**实质达成**，仅差「停止生成」「LLM 重试」两个小项。
**V1.5**（长期记忆✅已提前达成 / 工具❌ / 更多声音❌）。
**V2**（流式 TTS❌ / 自动朗读❌ / 长文本切句✅提前 / 音频队列🟡播放层 / 情绪化 TTS❌ / 声音状态❌ / 多 Agent❌）。

---

## 显式放弃项（有记录的）

1. **bat 改纯英文方案**（BAT_DEBUG_REPORT.md 方案 B）——用户否决，保留中文，最终以 GBK 编码解决。
2. **winsound SND_ASYNC 播放**——诊断（tools/diagnose_voice.py）证明在部分线程环境静默失效，弃用，改为工作线程同步播放。
3. **sqlite-vec / 向量索引、遗忘衰减策略**（docs/03 G6/G7）——主动推迟，非放弃。

## 需要提前修的架构问题（按优先级）

1. **ui/app.py Composition Root 膨胀**：拆 `ui/bootstrap.py`（构造依赖）+ 瘦身 app（只做装配与回调）。
2. **TTS 状态外显**：🔊 无合成中/失败反馈、TTS 掉线无横幅——设计 §16 明确要求，是体验最明显的缺口。
3. **停止生成 + LLM 退避重试**：流式一旦网络抖动，用户只能干等 30s 超时看到错误气泡。
4. **「新对话」UI 入口**：后端 reset() 已备，接一个按钮即可。
5. **voice/lang 硬编码入 config**：为 V1.5 多声音铺路。
6. on_reply_complete 事件钩子：低优先级（TTS 已由 UI 层接管且边界冻结）。
