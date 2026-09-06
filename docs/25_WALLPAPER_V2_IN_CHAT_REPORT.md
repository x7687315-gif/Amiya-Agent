# 壁纸方案 v2：壁纸住进聊天框 + 两侧主色调纯色

> 日期：2026-09-06
> 触发：docs/24 的「焦点锚定平移」方案被用户实测否决——壁纸堆向左侧、人物
> 被裁、且每张壁纸源尺寸不同导致表现不一。
> 用户明确设计意图（本次按此实施）：
> ① 壁纸只显示在**聊天框区域**（第二张截图框定的中栏范围），自适应其尺寸；
> ② 左右两侧与头部用**壁纸主色调相近的纯色**，整体不违和。

---

## 0. 一句话总结

壁纸从「全窗铺底 + 数学定位」改为「**内嵌中栏 + COVER 自适应**」：图片作为
中栏 Stack 的四向定位层，由布局天然跟随拖手柄/窗口变化——上一版全部手动
计算（焦点锚定、_apply_bg_layout、wallpaper_box）整体废弃，代码更少、行为
更符合直觉。

## 1. 上一版为何失败（复盘）

| docs/24 方案 | 实际效果（用户截图） | 根因 |
|---|---|---|
| 人物焦点锚定到左栏中心（left = panel_center − 0.38×img_w） | 壁纸整体堆向左侧、人物被裁掉一块 | 锚定使图片左移 ~300-500px，画作左部出窗；「跟随」的代价是构图失控 |
| 每张壁纸吃尺寸不同 | 表现不一 | 任何基于固定比例/锚点的数学定位都假设了画布构图，对异源尺寸天然脆弱 |

**教训**：用户说「适配聊天框」时，正确理解是**把壁纸放进聊天框区域做标准
背景适配（COVER）**，而不是把全窗壁纸做平移——后者是我在没有确认视觉预期
的情况下自行发明的方案。（本轮已先用 AskUserQuestion 确认预期选项，落地
后再次校准。）

## 2. 新方案（实施内容）

### 2.1 结构

```
root Stack
├── 全局底色：dominant 纯色（左右栏与头部透出它）
└── Column[header(透出纯色), body Row]
    ├── 左栏（透明 → 透出 dominant 纯色）
    ├── 中栏 Stack = 壁纸层 + 内容列
    │   ├── Image(COVER)          ← 四向定位，按中栏实际尺寸裁切填充
    │   ├── scrim（皮肤 scrim_opacity）
    │   ├── 左缘 48px dominant 渐变 ┐
    │   ├── 右缘 48px dominant 渐变 ├─ 纯色两侧与壁纸的过渡无硬边
    │   ├── 上缘 40px dominant 渐变 ┘
    │   └── 内容列（date_nav / tts_banner / chat_area / input_bar）
    └── 右栏（透明 → 透出 dominant 纯色）
```

- **COVER 自适应**：任何源尺寸的壁纸按中栏尺寸裁切填充、不变形——「吃尺寸
  不同」的壁纸零适配成本；拖手柄/窗口变化由布局天然跟随，无任何手动重算
  （_apply_bg_layout / wallpaper_box / ARTWORK_FOCUS_X / SKIN_CANVAS_RATIO
  全部废弃删除）。
- **边缘渐变融合**：左/右 48px、上 40px 向 dominant 渐变——两侧纯色与壁纸
  的接缝不可见（emil-design-eng：预防 jarring 过渡）。
- **可读性**：皮肤 scrim_opacity 照旧覆盖壁纸区。
- 走查（product-design:check 框架）对照：对比度（scrim 保障）✓、一致性
  （渐变端点=皮肤 dominant 同源）✓、异常态（无壁纸皮肤→纯 dominant 退化）✓。

### 2.2 关键实现事实（探针 v11 实证）

1. **嵌套 Stack 里 expand 不可靠**：Image 直接 expand、Container 包 Image
   expand 两次均未铺满；**四向定位（left/top/right/bottom=0）是唯一可靠
   填充机制**（FAB 已验证的同款路径）。`_build_middle_background` 因此返回
   「定位层列表」直接铺进中栏 Stack，不做额外嵌套。
2. flet 0.86 已无 `ft.alignment.center_left` 等预设，渐变锚点用
   `ft.alignment.Alignment(x, y)` 构造。
3. `ft.Column` 不接受 `padding`（探针两次踩中）——需要内边距时外包 Container。
4. `page.take_screenshot()`（enable_screenshots=True）可自主截屏验收 UI——
   本轮全部视觉结论都来自探针截图，非肉眼猜测。

## 3. 验收（probe v11 截图）

- 默认栏宽：壁纸铺满中栏、人物完整可见、两侧+头部纯色、边缘渐变融合无硬边；
- 左栏 260→400 拖宽：中栏变窄，壁纸 COVER 自动重排，无任何代码介入。

## 4. 测试

- `tests/test_scroll_and_wallpaper_fix.py` 壁纸段重写为
  `_build_middle_background` 契约（层构成/四向定位/渐变端点同源/短 hex/空退化）；
- 滚动契约测试不变（纯 Column 方案维持 docs/24 结论）；
- 全量：**316 passed / 5 skipped**（+1 已知真实 LLM 偶发用例，复跑即绿）。

## 5. 遗留说明

- ui-ux-pro-max 套件（用户截图所列选型层）未在可用技能清单中出现，
  `.qoder/skills/` 目录不存在——待用户确认安装位置后可再引入做选型层走查。
- 各皮肤 dominant_color 若与壁纸主色有偏差，改 skin.json 的 dominant_color
  即可（两侧纯色与渐变自动同源跟随）。
