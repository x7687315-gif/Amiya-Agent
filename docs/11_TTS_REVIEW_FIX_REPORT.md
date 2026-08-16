# 语音链路审查问题修复报告

> 日期：2026-08-16
> 基线 commit：`021f53f`（快照：M3/M4 语音集成与一键启动当前进度）
> 修复 commit：本文件所在提交（未 push，可随时 `git reset --hard 021f53f` 回滚）
> 验证结论：**基线 182 通过 / 5 跳过 → 修复后 198 通过 / 5 跳过（+16 回归测试，0 回归）**；
> bat 冒烟 3 连跑 0 报错 / 0 垃圾文件。

---

## 0. 最重要的发现：start_assistant.bat 的「方案 A」实际没修好

上一轮 `BAT_DEBUG_REPORT.md` 的结论是「加 UTF-8 BOM + 全角箭头」即可保留中文。
本次用**真实 cmd 执行冒烟测试**（假 GPT_DIR 走到环境检查失败退出）实证推翻了它：

| 方案 | 实测结果（各跑 3 次） |
|------|----------------------|
| UTF-8 + BOM + chcp（基线现状） | BOM 字节粘住首行 → `'锘緻echo' 不是内部或外部命令`，`@echo off` 失效全文件回显；chcp 中途切码后 cmd 文件偏移错位，REM 注释**尾部片段被当命令执行**（`'等待'/'y'/'坏' is not recognized`，即最初事故同款 token）。3/3 次出错 |
| UTF-8 无 BOM，首行 `@chcp 65001` | chcp 切码错位问题依旧，且**非确定**：3 次运行错误数 3/0/0 |
| **GBK(ANSI) 无 BOM，无 chcp（采用）** | cmd 原生按系统代码页解析，**3/3 次 0 错误、0 垃圾文件**，中文提示正常显示 |

根因补充说明：`chcp` 只影响显示层没错，但 cmd 解析批处理是**边读边执行、按字节偏移续读**的；
中途切换代码页会让偏移计算错位，跳到某行中间执行残句。BOM 则会污染首行命令。
唯一稳的做法就是让文件编码与系统 ANSI 代码页一致（中文 Windows = GBK），全程不切码。

已按采用方案修改 `start_assistant.bat`：保存为 GBK、删除 `chcp` 行、`@echo off` 保持首行，
并在文件头插入【编码约束】注释，防止将来被编辑器「好心」转回 UTF-8。

---

## 1. 修复清单（对应审查发现的问题）

### 高优先级

| # | 问题 | 修复 |
|---|------|------|
| 1 | `core/tts/service.py` 与 `tts_web/tts_web.py` 约 200 行复制代码，且已漂移（streaming_mode 一边 True 一边 2） | 新建共享模块 `core/tts/audio_utils.py`（**零第三方依赖，纯标准库**）：文本清洗/分块 + WAV 解析/拼接，单一实现。service.py 相对导入；tts_web 用 importlib 按文件路径加载（绕开 `core/tts/__init__.py` 连带 import requests/yaml，保住「GPT-SoVITS runtime 裸 Python 可跑」约束）。streaming_mode 统一为 `True` |
| 2 | 连点多个气泡 🔊 播放互相截断（winsound 进程级单声道，并发 PlaySound 互抢） | `AudioPlayer` 改为**单工作线程 + FIFO 队列**串行播放：入队即返回，逐条同步播出，播完删临时文件；工作线程死亡自动拉起。语义定为「排队串行」（不截断、不重叠） |
| 3 | `MuteState` 订阅无法退订；`ChatArea.clear()` 后闭包连着 IconButton 滞留订阅集合（泄漏） | `MuteState.unsubscribe()` 新增；`ChatBubble.assistant` 把订阅句柄挂到返回控件（`_mute_state`/`_mute_cb`）；`ChatArea.clear()` 清空前统一退订 |
| 4 | WAV 拼接不校验各块参数一致，异常参数会拼出变速/失真音频 | `concat_chunks_wav` 逐块校验（采样率/声道/位深），不一致抛错；service 捕获后降级为 `TTSResult.fail(INVALID_AUDIO)`，绝不静默产出坏音频 |
| 5 | `.gitignore` 缺口（27MB outputs、运行日志、用户个人文稿 user_essay.txt、版权敏感的 reference.wav 都可能被 `git add` 带入库） | 补：`logs/`、`TTS_FOREGROUND_LOG.txt`、`tts_web/_*.log`、`tts_web/outputs/`、`tts_web/user_essay.txt`、`core/tts/voice_profiles/*/*.wav`（沿用「游戏素材版权不入库」政策；yaml 入库并注明克隆后需自备 wav） |
| 6 | TTS 地址双源：bat 的 TTS_HOST/PORT 与 assistant.yaml 的 api.host/port 各写一份 | 文档级修复：两处互加【同步提醒】注释。（未做 bat 读 yaml——bat 内引号/转义脆弱，刚被编码事故教育过，不值得冒险） |
| 7 | `sample_rate=32000` 三处硬编码（拼接路径手里有 `params.framerate` 却不用） | 短文本路径解析 WAV 头取真实采样率（解析失败留 None）；长文本路径用 `params0.framerate` |
| 8 | 短文本路径发送未清洗的原文（零宽字符等直接进 API） | 统一改发 `chunk_text` 清洗后的块文本；纯标点/空文本直接短路返回空结果、不发请求 |

### 中低优先级

| # | 问题 | 修复 |
|---|------|------|
| 9 | 8-bit WAV 用有符号 `'b'` 解析会整体错位（GPT-SoVITS 只出 16-bit，属埋雷） | `pcm_to_int` 仅支持 16/32 位，其余显式 `ValueError`（8-bit 无符号场景直接拒绝而非产出直流偏置噪音） |
| 10 | `AssistantApp.__init__` 不初始化 mute_state/player/tts 等，run() 前访问 `AttributeError` | `__init__` 给默认值；`_speak` 增加 `tts is None` 守卫（未就绪静默跳过） |
| 11 | 合成请求飞行途中被静音，回来照样播 | `_speak` 合成返回后**二次检查**静音状态，作废不播 |
| 12 | `_worker` 错误气泡把原始异常 `{e}` 拼给用户 | UI 只显示固定话术，细节保留在 `logger.exception` |
| 13 | `test_m3_tts_ui.py` 模块级 monkey-patch `ft.Control.update` 泄漏到整个 pytest 会话 | 删除（conftest.py 的 autouse fixture 早已用 monkeypatch 正确覆盖同一需求） |
| 14 | tts_web：`_lock` 定义未用、`chunk_text` 内部 `import re`、health_check docstring 断行 | 随共享模块抽取一并清理 |
| 15 | bat：`chcp` 在 `@echo off` 前回显命令行、`:wait_ready` 标签从未被 goto | 一并处理（编码方案重做时自然消除） |

### 审查后核实为「非问题」的项

- `test_tts_service.py` 头注释「M2」：语音系列自己的里程碑编号（M1 参考音频 / M2 服务层 / M3 UI / M4 启动器），与 ROADMAP 旧编号并行，**不改**。
- BAT_DEBUG_REPORT 的全角箭头替换：确实必要（防止 REM 行 `->` 触发重定向建文件），保留。

---

## 2. 架构说明（响应「不要改架构」约束）

- 所有组件边界不变：TTSService / AudioPlayer / MuteState / ChatArea / Header 的公开 API 与调用关系照旧。
- 新增 `core/tts/audio_utils.py` 是纯函数工具模块（无状态、无第三方依赖），只是把两份重复实现收敛为一份。
- AudioPlayer 仍保持「play 立即返回、失败不抛异常」契约，内部从「每播放起一个线程」改为「唯一工作线程 + 队列」，对外行为只有一处语义变化：**连点改为排队而非互相打断**（这正是修复目标）。

## 3. 验证记录

1. **全量单测**：`.venv/Scripts/python.exe -m pytest tests/ -q`
   - 基线（stash 验证）：`182 passed, 5 skipped`
   - 修复后：`198 passed, 5 skipped`（5 个跳过 = 需真实 GPT-SoVITS 在 9880 端口的集成用例，按设计跳过）
   - 新增 `tests/test_tts_fixes.py` 16 个回归测试：8-bit 拒绝 / 拼接参数校验 / 清洗后文本 / 纯标点零请求 / 真实采样率 / FIFO 串行与临时文件清理 / 单工作线程 / 退订防泄漏 / 合成期静音 / tts_web 共享实现
2. **bat 冒烟**（假 GPT_DIR 逼环境检查失败、管道喂回车过 pause）：GBK 方案 3 连跑均 0 报错、0 垃圾文件、`请按任意键继续` 中文正常；UTF-8+BOM 与 UTF-8+@chcp 两方案复现错误（见上表）。
3. **import 验证**：`ui.app` / `core.tts.service` / `core.tts.player` 导入正常；`tts_web` 独立加载成功且 `chunk_text` 等绑定到 `core/tts/audio_utils.py` 同一源文件；audio_utils 无第三方依赖。
4. **git 验证**：`git status` 干净，垃圾文件均被新 .gitignore 挡住。

## 4. 回滚方式

```bash
git log --oneline -3          # 找到修复 commit 与基线 021f53f
git reset --hard 021f53f      # 整体回滚到修复前（未 push，安全）
```

只想回滚单个文件：`git checkout 021f53f -- <路径>`。

## 5. 遗留事项（本次不动，记录在案）

- 播放语义现为「排队串行」：长文本排队多条会等较久。若想要「新句打断旧句」，需要在 AudioPlayer 加代际号 + 跨线程 PlaySound 打断，风险较高，待真机体验后再定。
- 静音开关不中断**正在播放**的音频，只拦后续（含合成飞行窗口期，已二次检查兜住）。
- `reference.wav` 已被 gitignore：克隆仓库后需自备 `core/tts/voice_profiles/assistant/reference.wav`（yaml 有注释说明）。
- 集成测试（test_t1~t4 / health_check_up）需开着 GPT-SoVITS 才会跑，下次启动 TTS 后建议补跑一次确认真实链路。
- `BAT_DEBUG_REPORT.md` 的「方案 A（BOM）」结论已被本报告实证修正，保留作历史记录，勿再按它改回 UTF-8。
