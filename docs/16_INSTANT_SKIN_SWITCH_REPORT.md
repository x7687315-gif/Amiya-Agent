# 即时换肤 + 底部白条修复报告

> 日期：2026-08-17
> 用户问题：① 点击切换皮肤"没有反应"；② 底部仍是白色横条
> 验证：**全量 274 passed / 5 skipped（0 回归）**；用户真实点击三次（sakura→winter→sunset）
> 均即时生效且应用存活；截图像素证实 sunset 金橘壁纸 + 玻璃输入框。

---

## 一、"没反应"的三层真相（全部解决）

1. **旧实现是"重启生效"**（沿用冻结计划边界）：点击只写配置，界面不变——用户观感即"没反应"。
   → **改为点击即时生效**：`_apply_skin_runtime` 整体重建 UI（重读 .env → 重解析皮肤 +
   apply_skin 重写色板单例 → 重建全部控件 → 从库里回放今天的对话），壁纸/色板/头像全换。
2. **第一版即时切换有致命 bug**：在点击回调的调用栈里同步 `page.clean()` → Flet 会话被
   回收 → **窗口直接消失**（用户点 winter 时复现，日志铁证 `Session was garbage collected`）。
   → **修复：重建延迟到事件循环**（`page.run_task(_rebuild_ui_async)`，回调先返回再重建）；
   `_rebuilding` 防重入标志 + `finally` 复位；重建全程 try/except，失败绝不拖垮会话。
3. **点同款看不出变化**：应用此前已持久化 sakura 并随启动加载，用户再点 sakura 缩略图
   → 重建前后视觉相同。属预期行为（已选中项有描边高亮提示）。

## 二、底部白条修复

- 输入框本体原为近全实白 `SURFACE_SECONDARY(#F2F2F6)` 横贯底部，形成"白条"。
- 新增主题色 `SURFACE_GLASS = #C8F5F7FC`（78% 白的半透明玻璃），输入框改用——
  透出壁纸色调（sunset 下实测 R159 G137 B128，随皮肤变色），同时保持文字可读。
- InputBar 容器本身已透明（上一轮统一底色改造）。

## 三、其他修正

- `InputBar.focus()`：Flet 0.86.5 的 `focus()` 是协程，同步调用产生 RuntimeWarning——
  改为有事件循环/页面时正确调度、离线场景安全放弃。
- 左面板/抽屉文案从"重启后生效"改为"已切换为 …""点击即切换"。
- 生成回复期间点皮肤：只保存不重建（流式 worker 持有旧控件引用），回复完成后重启生效。

## 四、验证记录

| 项 | 结果 |
|---|---|
| 单元/契约 | 274 passed / 5 skipped（新增：延迟调度与防重入、玻璃色断言、选肤回调持久化+重建触发、生成中跳过重建、协程 focus 安全） |
| 用户真实点击 | sakura×5、winter、sunset——修复后点击全部触发 `运行时切换皮肤` 日志且进程存活（修复前 winter 点击导致会话回收，已复现并根治） |
| 截图像素（sunset 皮肤） | 聊天区 R248 G174 B120（金橘壁纸）✓ 输入区 R159 G137 B128（玻璃混色，白条消失）✓ 顶栏 R118 G125 B133（透壁纸）✓ |
| 文件 | `git status` 仅含本次预期改动 + 另一会话 proactive WIP（未触碰） |

## 五、提交内容

`ui/app.py`（延迟重建+防重入+focus 调度）、`ui/theme.py`（SURFACE_GLASS）、
`ui/components/input_bar.py`（玻璃底+协程 focus）、`ui/components/persona_status.py`、
`ui/components/persona_drawer.py`（即时切换文案）、`tests/test_skin_manager.py`（+4 测试）、本报告。

## 六、回滚

```bash
git reset --hard HEAD~1   # 未 push
```
