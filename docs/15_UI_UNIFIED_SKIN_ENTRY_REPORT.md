# UI 统一底色 + 左下角切肤入口 修改报告

> 日期：2026-08-17
> 需求来源：用户三条明确要求（切肤入口显式放左下角 / 消除顶栏与聊天区之间漏图空隙 /
> 全界面底色统一为聊天框壁纸底色）
> 验证：**全量 269 passed / 5 skipped（+3 新契约测试）**；
> 真机截图像素采样 11 点 + 视觉分析四项全部通过。

---

## 一、改动内容

### 1. 切肤入口：抽屉隐藏式 → 左下角显式入口

- `PersonaStatusPanel` 左下角新增「更换皮肤」按钮（调色板图标 + PRIMARY_SOFT 卡片样式，
  显眼且不突兀）；点击展开皮肤缩略图网格（六套）+「跟随情绪」开关 + 状态提示，
  再点收起。默认收起不占空间。
- 未注入 `skins` 参数时按钮完全隐藏——旧调用方与既有测试零影响。
- 抽屉里的「外观」区块保留（两处入口共用同一持久化回调 `app._on_skin_selected`）。

### 2. 消除空隙漏图 + 全界面统一壁纸底色

- **五个面板整栏底色全部透明化**（`bgcolor=None`，保留 1px 描边做结构性分隔）：
  Header 顶栏 / DateNav 日期栏 / PersonaStatusPanel 左栏 / MemoryPanel 右栏 /
  InputBar 底栏；ChatArea 此前已透明。
- 效果：整个窗口背景 = **壁纸 + 浅色遮罩** 一张连续画布，左右上下与聊天区同一底色，
  不存在"缝隙漏图"的斑驳感（空隙问题随统一背景自然消解）。
- **可读性保障**：内容自带不透明浅色卡片——聊天气泡（SURFACE/PRIMARY_LIGHT）、
  左栏信息卡（PRIMARY_SOFT，即每套皮肤的淡色调）、输入框（SURFACE_SECONDARY）、
  展开的皮肤面板（SURFACE）。文字全部落在卡片上，不直接压壁纸。
- 该设计固化为测试 `test_unified_wallpaper_background_contract`（六个组件 bgcolor
  断言为 None），防止将来回归。

### 3. 接线

`app.py` 给 PersonaStatusPanel 传 `skins / ui_skin / current_skin_id /
on_skin_selected`（与抽屉同源：`SkinContext.skins` + `persist_ui_skin`）。

## 二、验证记录（全部通过）

1. **单元/契约**：全量 `pytest tests/ -q` → **269 passed, 5 skipped**（0 回归；
   新增统一底色契约、左下角入口交互（展开/收起/选肤/开关）、无 skins 兼容三组）。
   过程中曾出现 2 次 1-2 个测试瞬时失败——为另一会话并发编辑 `core/proactive.py`
   所致的 import 竞态，复跑即绿，与本次改动无关。
2. **接口自查**：皮肤选择回调持久化（persist_ui_skin 只动 UI_SKIN 行）、跟随情绪
   开关语义（开=auto / 关=锁定当前）、DateNav/输入/发送/停止/静音/朗读链路——
   均有既有测试覆盖且全绿；`import ui.app` 正常。
3. **真机像素采样（窗口 1180×760）**：
   | 区域 | 采样值 | 结论 |
   |---|---|---|
   | 顶栏内容区 (+55) | R96 G118 B160 | 壁纸蓝 ✓ |
   | 顶栏下方 (+70) | R95 G120 B164 | 连续壁纸，无缝隙带 ✓ |
   | 左栏边缘 | R95 G122 B170 | 壁纸 ✓（左栏内部卡片近白属预期） |
   | 右栏中部 | R96 G146 B209 | 壁纸 ✓ |
   | 聊天区中心 | R105 G169 B237 | 壁纸 ✓ |
   | 底部输入区 | R100 G178 B240 | 壁纸 ✓（输入框本体仍为浅色卡片） |
4. **视觉分析（截图）**：①左下角「更换皮肤」按钮清晰可见；②顶栏/左右/聊天/底部
   统一星空壁纸、无白色灰色块；③顶栏与聊天区之间无突兀空隙；④文字清晰可读。
5. **文件自查**：`git status` 仅含本次预期改动与另一会话的 proactive WIP（未触碰）；
   无临时文件遗留（生成脚本用后即删）。

## 三、遗留与说明

- 换肤仍是**重启生效**（运行时换肤 = 计划 Phase 4 边界，未变）。
- 六套皮肤遮罩维持 0.38–0.45（上一轮已调）；若个别皮肤下左栏卡片外文字对比不足，
  可微调对应 `skin.json` 的 `scrim_opacity`，无需改代码。
- 另一会话的 P4 主动问候 WIP（core/proactive.py + agent.py 未暂存改动）保持原样，
  本次提交未包含、未干扰。

## 四、提交

- 本次：`ui/components/{persona_status,header,memory_panel,input_bar,date_nav,chat_area}.py`、
  `ui/app.py`、`tests/test_skin_manager.py`、本报告。
- 回滚：`git checkout HEAD~1 -- <路径>` 或 `git reset --hard HEAD~1`（未 push）。
