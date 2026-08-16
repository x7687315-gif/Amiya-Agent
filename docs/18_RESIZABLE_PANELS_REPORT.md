# 可调分栏（聊天区尺寸/位置手动调节）报告

> 日期：2026-08-17
> 用户需求：聊天框尺寸能手动调节、移动到合适位置（壁纸左置后人物可能被聊天栏遮挡）
> 验证：**全量 280 passed / 5 skipped（+5 新测试，0 回归）**；用户实际拖拽使用中，
> 栏宽持久化生效（.env 记录 UI_LEFT_WIDTH=180 / UI_RIGHT_WIDTH=339）。

---

## 一、实现

### 1. 新组件 `ui/components/split_handle.py`（SplitHandle）

- 左右两根竖向调节手柄，夹在 [左栏 | 聊天区 | 右栏] 之间（宽 9px，不占视觉空间）
- `RESIZE_COLUMN` 光标 + 悬停淡紫高亮，可发现性好
- 水平拖拽增量回调（`on_resize(delta_x)`，drag_interval=10ms 平滑），
  松手 `on_done()` 触发持久化；组件纯 UI，不知道两侧是什么面板

### 2. 布局接线（app.py）

- `body` Row 改为 `[左栏, 手柄, 聊天区(自适应), 手柄, 右栏]`，spacing=0
- 拖左手柄：左栏加宽（180–560px）——聊天区**右移**给壁纸人物让位
- 拖右手柄：右栏收窄（220–620px）——聊天区**变宽**
- 两端钳制防拖没；拖动中 `width + update()` 实时重排

### 3. 持久化（config.py）

- `Settings.ui_left_width / ui_right_width`（env `UI_LEFT_WIDTH=260` /
  `UI_RIGHT_WIDTH=300` 默认，启动时钳回合法区间）
- `persist_ui_layout(left, right)`：松手时写 .env，只动这两行；
  运行时换肤重建后从 settings 恢复（布局与皮肤互不干扰）

## 二、验证

1. **单元**：280 passed / 5 skipped——新增：env 覆盖与默认值、persist 替换/追加
   且保留其它行、手柄增量转发与 done 回调、左右拖拽钳制边界、持久化调用。
2. **实机（用户本人操作）**：用户拖动左栏手柄至下限 180、右栏至 339，
   `.env` 如实持久化；重启/换肤后布局保持；应用全程 0 报错。
3. **截图**：面板宽度即用户自调值，壁纸人物可视区域随布局变化，整体连续。

## 三、说明

- 聊天区宽度 = 窗口 − 左右栏 − 手柄，自动自适应；只调两侧即可移动/缩放聊天区
- 布局与壁纸方案正交：任何皮肤下都可自行调节
- 拖拽中无明显卡顿（增量式 width 更新）；如后续需要拖影优化可给手柄加
  拖动中的视觉指示

## 四、提交

`ui/components/split_handle.py`（新）、`ui/app.py`、`config.py`、
`tests/test_skin_manager.py`（+5）、本报告。

回滚：`git reset --hard HEAD~1`（未 push）。
