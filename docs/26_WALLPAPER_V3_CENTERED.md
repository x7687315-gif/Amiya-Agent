# 壁纸方案 v3：居中 + 边色延伸 + 裁掉烘焙带

> 日期：2026-09-06
> 触发：v2（docs/25）用户实测反馈——「壁纸没有居中」「一圈非常严重的黑边把
> 壁纸压缩在这里」「两侧颜色突兀、压抑」。用户口径：①壁纸放正中间；②周围以
> **壁纸周围那一圈颜色 / 主色调占比最多的颜色**为基准慢慢延伸填满，整体平和。

---

## 0. 一句话总结

三层根因逐一定位并修复：①侧栏灰色来自 skin.json 手填 `dominant_color`
（pale=#A29DA0），与壁纸真实边色严重不符 → 改为**从壁纸采样**；②v2 的 COVER
裁切在竖长聊天框里切出偏心画面且 v2 渐变暗边形成"黑框" → 改为 **CONTAIN
居中完整显示**；③画布内**烘焙的延伸色带**（旧方案遗留，pale 右侧 604px 灰带）
在 CONTAIN 下原样露出 → 用工具**裁掉烘焙带**（源图备份为
`background_source.jpg`）。探针 v12 截屏验收：壁纸居中铺满聊天框、无黑边、
四周色彩同源平和。

## 1. 根因与修复对照

| 用户所见 | 根因 | 修复 |
|---|---|---|
| 一圈严重黑边、压抑 | skin.json 手填 dominant（pale #A29DA0 中灰）≠ 壁纸真实边色（亮浅彩）；v2 又用它做 48px 暗渐变边 | `tools/sample_skin_dominant.py` 从壁纸采样四边环均值色写入 skin.json（`edge_colors` + `dominant_color`=整环均值）；v2 暗渐变边整体删除 |
| 壁纸被压缩、不居中 | v2 COVER 在竖长中栏裁出偏心切片 | `_build_middle_background` v3：CONTAIN 居中完整显示（不裁不压） |
| 画面里一块灰板 | 画布烘焙的延伸色带（pale 右 604px / starry 底 162px / sakura·sunset·warm 右 960px / winter 右 1023px）在 CONTAIN 下露出 | `tools/bake_skin_canvas.py` 自动检测平坦带（列/行灰度 σ<3，裁切上限 40%）裁掉；原图备份 `background_source.jpg` 供再烘焙 |
| 两侧颜色突兀 | 侧栏/头部用异色灰 | 侧栏/头部/周围填充全部同源：整环均值色；中栏周围=顶边色→环均值→底边色垂直渐变，"慢慢延伸开" |

## 2. v3 中栏背景结构（`_build_middle_background`）

```
中栏 Stack（四向定位层，probe v11 实证 expand 不可靠）
├── 周围填充：LinearGradient(顶边色 → 环均值 → 底边色)   ← 逐边延续壁纸自身边色
├── 壁纸：Image(fit=CONTAIN) 居中完整显示
└── scrim：dominant 同色系，皮肤 scrim_opacity（可读性）
```

- skin.json 新增 `edge_colors: {top,bottom,left,right}`（采样值）；
  旧皮肤无该字段时渐变退化为纯 dominant（有测试）。
- 采样/裁切均为**开发期工具**（Python39 + PyQt5，运行时零新增依赖）：
  `tools/sample_skin_dominant.py`、`tools/bake_skin_canvas.py`。

## 3. 各皮肤裁切与采样结果

| 皮肤 | 裁切 | 裁后尺寸 | 环均值（侧栏/头部） |
|---|---|---|---|
| pale | 右 604 | 1956×1600 | #CACBCC |
| sakura | 右 960 | 1600×1600 | #E6DEDA |
| starry | 底 162 | 2560×1438 | #022D63 |
| sunset | 右 960 | 1600×1600 | #292329 |
| warm | 右 960 | 1600×1600 | #E6DEDA |
| winter | 右 1023 | 1537×1600 | #B5B7C0 |

## 4. 验收

- 探针 v12（1920×1040，真实 skin.json + 真实构建函数）截屏：壁纸居中、
  铺满聊天框、无灰块无黑边；头部/左栏/右栏与画作同源浅灰，整体平和；
- 拖宽左栏场景：中栏变窄，CONTAIN 自动重排（布局驱动，零手动计算）；
- 全量测试 **317 passed / 5 skipped**（壁纸契约测试同步 v3：3 层结构、
  CONTAIN、渐变端点、edge_colors 回退）。

## 5. 遗留

- 若今后重烘画布：把新图放 `background_source.jpg` 再跑
  `bake_skin_canvas.py`（裁带 + 重采样一步完成）。
- ui-ux-pro-max 套件仍未在可用技能清单中出现（项目无 `.qoder/skills/`），
  选型层走查待用户确认安装位置后进行。
