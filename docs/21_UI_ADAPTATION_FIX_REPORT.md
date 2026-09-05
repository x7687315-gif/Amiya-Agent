# UI 适配问题修复报告（P1–P7）

> 日期：2026-09-06
> 背景：壁纸左置 + 可调分栏上线后，把布局拖到极端位（左 180 / 右 567，窗口
> 1180×760）暴露出 7 个「当时只考虑窗口尺寸适应、没考虑其它 UI 元素」的适配
> 欠账：气泡宽度失控、分栏可把聊天区挤死、日期栏裁字、壁纸左缘被裁、左栏
> 竖排换行、右栏贴边、气泡边距过重。逐项诊断后修复，本文记录落地方式、
> 与诊断建议的差异点、以及回归验证结果。

---

## 0. 一句话总结

诊断报告里的 7 个问题全部修复；核心思路是把散落两处、互不联动的「聊天区宽度
相关魔法数」收敛成单一真源 `ui/layout_metrics.py`（纯函数、可测），并新增
17 个回归测试。全量测试 **297 passed / 5 skipped / 0 failed**。

## 1. 逐项落地方式

### P1 · 聊天气泡宽度失控 ✅（采用方案 A）

- 死参数 `max_width_ratio` 现在真实生效：`ChatBubble.user/assistant` 新增
  `available_width` 参数，气泡外层容器（cap）宽度 =
  `bubble_max_width(available_width, ratio, reserved)`。
- **「至多」语义**：cap 带 `alignment`，Flet/Flutter 下容器给子元素**松约束**——
  短消息仍是内容宽（右/左对齐），长消息才被 `ratio × 聊天区宽` 截断。
  实测确认 `ft.Container` 无 `max_width`、无 `ConstrainedBox`（与诊断一致），
  这是 0.86.5 下唯一能表达"至多"的做法。
- `expand=bool(on_speak)` 已删除：改为行尾加一个**弹性占位**把 🔊 推到最右，
  气泡本体不再无条件撑满。
- `reserved` 修正：气泡行内还有头像 32 + 朗读键 40 + spacing 24 + margin 8，
  窄幅下 `ratio × 宽` 会比剩余空间还大导致溢出，故取
  `min(ratio × 宽, 宽 − reserved)`——宽幅保持设计比例，窄幅保证不溢出。
- 宽度变化走 `ChatBubble.apply_width` **回写容器**而非重建气泡：保住流式输出
  与 🔊 的静音/忙碌订阅（重建会打断正在生成的回复）。

### P2 · 可调分栏钳制与窗口宽度解耦 ✅

- `LEFT_MIN/LEFT_MAX = 180/560`、`RIGHT_MIN/RIGHT_MAX = 220/620` 静态常量已删，
  改为 `ui/layout_metrics.py` 的 `clamp_left_width / clamp_right_width`：
  动态上限 = `窗口宽 − 对侧 − 2×手柄(18) − 聊天区保底(420)`。
- 三个同步时机全部接线：拖手柄（`_drag_*`）、窗口 resize（`page.on_resize` →
  `_on_window_resize`）、换肤整体重建（重建后 `_build_ui` 末尾 `_sync_chat_width`）。
- 持久化值重校：`.env` 恢复的栏宽经 `clamp_pair` 按默认窗口宽重校一次，
  窗口就绪后 `_setup_page` 再校一次（防换显示器带出非法布局）。
- **顺序敏感**（诊断未预见的坑）：必须**先钳左、再以新左钳右**。若先钳右，
  左栏被抬到地板值后会吃掉聊天区宽度，右栏仍按旧左栏保留过大值，
  聊天区跌破 420（`clamp_pair` 与 `_on_window_resize` 已统一为左先序，
  并有测试 `test_p2_mutual_clamp_same_order_as_resize_handler` 守护）。

### P3 · 日期导航栏窄幅裁字 ✅（组合拳，取 1+2 项）

- Dropdown 选项文本改紧凑格式 `format_day_compact`（"9月6日 · 周日"，省年份），
  完整格式挂 Dropdown 的 `tooltip`；
- 聊天区栏宽 < 480 时「回到今天」退化为纯图标（`_today_btn_icon`，
  与文字版互斥显隐，语义由 tooltip 承担）；
- 未采用"包固定最小宽容器 + 横向滚动兜底"：紧凑格式 + 图标化后 350px 行宽
  已足够容纳，引入横向滚动反而破坏时间轴交互。

### P4 · 壁纸 COVER 居中裁切吃掉左缘 ✅（**优于诊断建议**）

诊断推荐方案 A（重烘画布到 4096×1600）。实施时发现**方案 A 的前提不成立**：
COVER 的裁切锚点是居中，画布加宽后左右被裁的**绝对像素**反而更多
（4096 画布在 1180 窗口下左缘被裁 ~383px，比 2560 画布的 18px 严重得多）。

实际采用**零重烘、零升级**的运行时方案：

```
ft.Container(alignment=ALIGN_CENTER_LEFT,
             content=ft.Image(fit=ft.BoxFit.COVER, width=1.6×H, height=H))
```

- 给 Image **显式盒子**（高=窗口高，宽=画布比例×高），盒子比例==画布比例，
  COVER 不产生裁切；容器左对齐定位，溢出窗口右缘的部分本就是延伸色，
  被窗口自然截断（无需 clip_behavior）。
- 高瘦窗口（1000×900）：左缘完整 ✓；超宽窗口（1920×800）：右侧由底层
  dominant 色板无缝补齐 ✓。
- 画布比例常量 `SKIN_CANVAS_RATIO = 2560/1600` 落在 `ui/design/skin.py`
  （画布规格的归属地），重烘尺寸若变只改这一处。
- resize 时 `_apply_bg_size()` 重算，左缘始终贴合。

### P5 · 左栏 180px 下内容截断/竖排换行 ✅

- `LEFT_MIN` 180 → **220**（`layout_metrics.LEFT_MIN`，钳制与面板下限同一来源）；
- 身份行（名字/称号）与最近记忆条目补
  `overflow=ELLIPSIS, max_lines=1` + `tooltip` 全文；
- 皮肤入口未做图标化退化：220 下内容宽 188px 足够容纳「更换皮肤」按钮，
  且按钮本就有 tooltip（保持改动最小，符合 Minimal Scope）。

### P6 · 右栏底部贴边 + 滚动条不可见 ✅

- 滚动列末尾追加 `Container(height=sp.SM)` 尾垫；
- `ScrollMode.AUTO` → **`ALWAYS`**（采纳诊断倾向："还有更多内容"的可供性
  比视觉干净重要）。

### P7 · 窄聊天区下 64px 让位边距过重 ✅

- margin = `bubble_side_margin(chat_width)` = `min(64, 8% × 聊天区文本宽)`；
- 与 P1 共用同一 `available_width`（同一计算源，无第二份魔法数）；
- 助手气泡右侧的固定 64px 直接由"cap 上限 + 弹性占位"取代
  （剩余空间自然留白，天然响应式）。

## 2. 新增/修改文件

| 文件 | 变更 |
|---|---|
| `ui/layout_metrics.py` | **新增**：宽度关系单一真源（纯函数，不 import flet） |
| `ui/app.py` | P2 动态钳制 + resize 接线 + 持久化重校；P4 壁纸左锚；宽度注入 |
| `ui/components/chat_bubble.py` | P1 cap + reserved；P7 响应式边距；`apply_width` 回写 |
| `ui/components/chat_area.py` | 承接 `set_chat_text_width`，所有气泡工厂调用传入宽度 |
| `ui/components/date_nav.py` | P3 紧凑格式 + `set_width` 紧凑形态切换 |
| `ui/components/persona_status.py` | P5 省略策略 + tooltip |
| `ui/components/memory_panel.py` | P6 尾垫 + 滚动条常显 |
| `ui/design/skin.py` | 新增 `SKIN_CANVAS_RATIO`（画布规格归属地） |
| `ui/theme.py` | 新增 `ALIGN_CENTER_LEFT` |
| `tests/test_ui_layout_metrics.py` | **新增**：17 个回归测试 |
| `tests/test_skin_manager.py` | 拖拽钳制测试按 P2 新契约更新（旧断言固化的是被修掉的缺陷） |

## 3. 回归验证

```
Before（基线 d0934e4）：295 passed / 5 skipped（含 1 个按旧契约断言的钳制测试）
After：                 297 passed / 5 skipped / 0 failed
                        （+17 新增布局测试；钳制测试按新契约更新）
```

- 5 个 skipped 均为需真实 GPT-SoVITS 服务（127.0.0.1:9880）的集成用例；
- 期间出现过 1 例真实 DeepSeek 抽取用例偶发失败（LLM 输出波动），单独重跑通过，
  与本次 UI 改动无关（本次只改 `ui/*`）。

### 验收清单对照

| # | 验收项 | 状态 |
|---|---|---|
| 1 | 极端拖拽位下聊天区 ≥420px、无裁字/竖排 | ✅ 由 P2 钳制保证（有测试） |
| 2 | 短气泡 ≤78% 聊天区宽、🔊 靠右 | ✅ P1 cap + 弹性占位（有测试） |
| 3 | 高瘦窗口左缘壁纸完整；超宽窗口右侧无缝 | ✅ P4 等高左锚（逻辑保证） |
| 4 | 记忆面板尾垫 + 滚动条常显 | ✅ P6 |
| 5 | 全量 pytest 通过 + 新增回归测试 | ✅ 297/5 |

> 其中 1–4 项以"代码结构保证 + 单测"形式验收；最终视觉确认需在真机
> 拖拽验证（自动化测试无法截屏比对）。

## 4. 与诊断建议的差异点（重要）

| 项 | 诊断建议 | 实际落地 | 原因 |
|---|---|---|---|
| P4 | 方案 A 重烘 4096×1600 | 运行时等高左锚 | 方案 A 前提不成立：COVER 居中裁切下画布加宽反而裁得更狠 |
| P2 | 动态钳制（未提顺序） | 动态钳制 + **先左后右** | 先钳右会把左栏顶到地板后仍允许右栏过宽，保底失守 |
| P1 | `width=min(自然宽, ratio×区宽)` | cap 松约束 + **reserved 修正** | Flet 拿不到"自然宽"；且行内头像/朗读键会吃掉空间 |
| P3 | 三项组合拳 | 取 1+2 项 | 第三项（横向滚动兜底）在本场景收益为负 |
