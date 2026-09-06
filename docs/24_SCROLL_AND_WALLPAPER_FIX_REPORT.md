# 滚动到底 & 壁纸跟随聊天框 修复报告

> 日期：2026-09-06
> 触发：用户实测反馈两问题——①滚轮/拖滚动条/点箭头都无法看到最新消息；
> ②壁纸没有跟随聊天框调整。
> 方法：系统化调试（根因先行）+ 自主探针遥测/截屏取证（v4–v10）。

---

## 0. 一句话总结

问题 1 的真凶是 **Flet 0.86.5 ListView 懒构建的 extent 估算陷阱 + scroll_to 与
auto_scroll 互斥**（估算低估 → 滚动被钳在假底部 → 触底瞬间位置修正把视图
**传送回顶部**）；改用纯 `Column(scroll=ALWAYS)` 后 extent 与理论值精确一致
（probe v7-D：1149.6 == 手算值），跳底无重置。问题 2 的壁纸**尺寸其实一直
是对的**（接缝位置 ≈ pale 画布 76% 画作边界的投影），真问题是**构图**——人物
被聊天区遮挡且拖动分栏时壁纸不动；改为「人物焦点锚定」随聊天框重算。

## 1. 问题 1：滚动到底（探针证据链）

| 探针 | 变量 | 结果 |
|---|---|---|
| v1/v3 | 真实 ChatArea + 遥测 | 填充 8 轮后 believed max=1306；scroll_to(-1) 动画途中 max 剧烈震荡（1099↔1306）；**触底（OVERSCROLL at max）瞬间 START pixels=0——传送回顶** |
| v4 | + 截屏 | 跳底×3 后截图：视图停在消息 0–2（复现用户所见） |
| v7 | 四场景二分 | 固定高度 Container / 纯 Column + 同样气泡：**pixels==max==手算真值，零重置** |
| v5/v6/v8/v9 | ListView↔Column、on_demand、FAB update stub | 逐值相同的震荡 → 排除估算/FAB；锁定「滚动容器实现 + auto_scroll 语义」 |

三个叠加的根因：

1. **ListView 懒构建 extent 估算**：滚动被钳在估算 max 内，未构建尾部永不
   布局、永不修正（believed 1306 vs 真实 ~1740）。
2. **触底位置修正**：首次到达估算末端时，新构建子项的真实高度触发修正，
   把滚动位置打回顶部（用户感知：永远差一截、拉到底会被弹回去）。
3. **scroll_to 与 auto_scroll 互斥**（flet 文档明言 True 时无效）：旧 FAB
   回调先置 `auto_scroll=True` 再 scroll_to，自我禁用——箭头从第一天起就是
   装饰；连带的 `build_controls_on_demand=False` 修复尝试会破坏子项宽度
   约束（文本不换行），弃用。

**修复**（`ui/components/chat_area.py`）：

- 滚动容器 `ft.ListView` → **`ft.Column(scroll=ft.ScrollMode.ALWAYS)`**
  （extent 精确、滚动条常显可抓、无估算无修正）；
- 放弃 Flutter 侧 auto_scroll 钉底，**显式 `scroll_to(offset=-1)` 跟随**：
  `_scroll_if_auto` 在每次追加/流式增量后跳到真实底部，`_on_scroll` 只维护
  跟随标志与 FAB 可见性，`_async_scroll` 不再做任何标志切换；
- FAB 回调不再设置 auto_scroll（新容器上 scroll_to 恒有效）。

**验收**（probe v10 截屏）：3× 跳底后视图停在真实底部——第 7 条（最后一条）
回复完整可见带底部留白，滚动条拇指位于轨道底端，FAB 正确隐藏。

## 2. 问题 2：壁纸跟随聊天框

画布取证：pale 画布 = 左 76% 画作 + 右 24% 烘焙延伸色；按「等高左锚」正确
尺寸换算，画作/延伸色边界应落在窗口 65.4%——与用户截图的接缝位置吻合，
即**尺寸无误**。用户预期（本次确认）：拖动分栏/调整聊天框时，人物应跟着
调整可见区域，不被聊天区盖住关键画面。

**修复**（`ui/app.py` + `ui/layout_metrics.py` + `ui/design/skin.py`）：

- 新增纯函数 `wallpaper_box(page_height, window_width, focus_anchor_x,
  artwork_focus_x, canvas_ratio) -> (w, h, left)`：
  - 高度基准改为 **page.height（客户区）**——原 window.height 含标题栏
    （约 +38px）导致壁纸比可视区高、垂直偏移；
  - **人物焦点锚定**：把画布内人物关键画面（`ARTWORK_FOCUS_X=0.38`，取自
    pale 眼部实测 ≈0.39）锚定到左栏（无遮挡区）中心，`left` clamp ≤0 保证
    左缘永不露底；
- Image 改为 bg Stack 的**直接定位子项**（left/top 仅对直接子项生效）；
- 重算时机接线：窗口 resize（原有）+ **`_sync_chat_width`**（拖手柄/换肤
  重建都会经过）——壁纸真正"跟随聊天框"。

**验收**（probe v10 截屏对比）：左栏 220→400 拖宽后人物右移 ~90 逻辑像素
（探针日志 face 111→201），全程完整可见、左缘贴合；不同窗口高度下壁纸
不再出现 38px 垂直偏移。

## 3. 测试与回归

- 新增 `tests/test_scroll_and_wallpaper_fix.py`（6 项）：Column 契约、FAB
  行为、`wallpaper_box` 五个边界（客户区高度/画布比例/焦点锚定/左缘钳制/
  窄窗覆盖）；
- 更新 `test_ui_components.py` 滚动暂停测试至新契约（旧断言固化的是
  "FAB 点击立即恢复 auto"这一被修掉的缺陷行为）；
- 全量：**317 passed / 5 skipped / 0 failed**（5 个 skipped 仍为需真实 TTS
  服务的集成用例）。

## 4. 已知取舍

- 非懒构建 → 一次性布局全部气泡：按天会话量级（数百气泡）可接受；若未来
  单日消息量级膨胀，可回退 ListView 并改用 `scroll_key` 跳转（flet 文档
  认可的动态列表定位方式，本次未验证）；
- 流式跟随由"每增量一次 scroll_to"实现，delta 密集时动画互相收敛到底部，
  实测平滑；`auto_scroll_animation` 微调留待需要时再开。
