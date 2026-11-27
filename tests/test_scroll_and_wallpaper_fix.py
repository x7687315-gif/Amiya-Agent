"""聊天区滚动契约 + 壁纸「住进聊天框」布局（2026-09-06 用户实测两问题修复）。

问题 1（探针 v4-v9 实证）：Flet 0.86.5 ListView 懒构建的 max_scroll_extent 按
已构建子项估算、滚动被钳在估算值内，触底时位置修正把视图传送回顶部——滚轮/
拖滚动条/scroll_to 全部到不了底；且 scroll_to 与 auto_scroll 互斥（文档明言）、
build_controls_on_demand=False 破坏子项宽度约束。修复：改用纯 Column(scroll=
ALWAYS)（extent 精确，probe v7-D 实证）+ 显式 scroll_to 跟随 + 滚动条常显。

问题 2（用户方案 v2，docs/25）：壁纸「住进」聊天框（中栏）——COVER 按中栏
实际尺寸裁切填充，任何源尺寸都不变形（"每张壁纸吃尺寸不同"无需逐张适配）；
左右两侧与头部透出全局 dominant 纯色；边缘 48/40px 渐变融合避免硬边。
上一版「焦点锚定平移」方案被用户否决（壁纸堆向左侧、裁掉人物），已整体废弃。
"""
from __future__ import annotations

import pytest

from ui.app import AssistantApp


# ---------------------------------------------------------------------------
# 问题 1：滚动契约
# ---------------------------------------------------------------------------


def test_chat_uses_plain_column_scroller():
    """滚动容器必须是纯 Column(scroll=ALWAYS)：extent 与真实内容精确一致。

    探针实证（v4-v9，2026-09-06）：ListView 懒构建的 extent 按已构建子项估算，
    滚动被钳在估算值内且触底时把视图传送回顶部（滚轮/拖滚动条/scroll_to 全部
    到不了底）；build_controls_on_demand=False 破坏子项宽度约束（文本不换行）；
    纯 Column 的 extent 与手算真值精确一致（probe v7-D：1149.6 == 理论值）。"""
    from ui.components.chat_area import ChatArea
    from core.persona import load_persona
    import flet as ft

    area = ChatArea(load_persona())
    assert isinstance(area._list, ft.Column)
    assert area._list.scroll == ft.ScrollMode.ALWAYS
    assert area._list.auto_scroll is False  # 显式 scroll_to 跟随，不依赖钉底


def test_fab_hides_button_and_lands_at_bottom():
    """FAB 回调：隐藏按钮 + 调度跳底（无需任何 auto_scroll 标志切换）。"""
    from ui.components.chat_area import ChatArea
    from core.persona import load_persona

    area = ChatArea(load_persona())
    area._scroll_to_bottom(None)  # 无 page：run_task 分支跳过
    assert area._scroll_btn.visible is False


# ---------------------------------------------------------------------------
# 问题 2：壁纸「住进」聊天框（_build_middle_background 契约）
# ---------------------------------------------------------------------------


def _tmp_png(tmp_path, name="bg.png"):
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 16)  # 最小 PNG 头（构造期不读内容）
    return p


def test_middle_background_empty_when_no_wallpaper(tmp_path):
    """壁纸路径无效 → 返回空列表：中栏退化为纯 dominant（与无壁纸皮肤一致）。"""
    assert AssistantApp._build_middle_background(None, "#FFECEFF1", 0.42) == []
    assert AssistantApp._build_middle_background(
        tmp_path / "missing.png", "#FFECEFF1", 0.42) == []


def test_middle_background_layers(tmp_path):
    """v3 壁纸层 = 边色渐变延伸 + 居中 CONTAIN 壁纸 + 同色 scrim（docs/26）。

    - 壁纸**居中**完整显示（CONTAIN，不裁切不压缩——v2 COVER 被用户否决：
      "被压缩+一圈黑边+不居中"）；
    - 周围填充 = 壁纸自身四边环均值色的垂直渐变（顶边色→环均值→底边色），
      "以壁纸周围那一圈颜色为基准慢慢延伸开"，接缝同源、整体平和；
    - 全部层四向定位（left/top/right/bottom=0）：嵌套 Stack 的 expand 语义
      不可靠（probe v11 实证），定位子项是已验证可行的机制。"""
    import flet as ft

    edge = {"top": "#CFC7C6", "bottom": "#A6ADB3",
            "left": "#F5F5F5", "right": "#A29DA1"}
    layers = AssistantApp._build_middle_background(
        _tmp_png(tmp_path), "#C3C1C3", 0.45, edge_colors=edge)
    assert len(layers) == 3
    surround, img_box, scrim = layers

    for positioned in layers:
        assert positioned.left == 0 and positioned.top == 0
        assert positioned.right == 0 and positioned.bottom == 0

    # 周围延伸：顶边色 → 环均值 → 底边色 的垂直渐变
    assert surround.gradient.colors == ["#CFC7C6", "#C3C1C3", "#A6ADB3"]

    # 壁纸居中完整显示
    assert isinstance(img_box.content, ft.Image)
    assert img_box.content.fit == ft.BoxFit.CONTAIN

    # scrim 同色系（dominant），保证可读且不引入异色
    assert scrim.bgcolor == "#C3C1C3"
    assert scrim.opacity == 0.45


def test_middle_background_edge_colors_fallback(tmp_path):
    """skin.json 无 edge_colors（旧皮肤）时渐变退化为纯 dominant——不平移不报错。"""
    layers = AssistantApp._build_middle_background(
        _tmp_png(tmp_path, "b2.png"), "#ECEFF1", 0.4)
    surround = layers[0]
    assert surround.gradient.colors == ["#ECEFF1", "#ECEFF1", "#ECEFF1"]


def test_middle_background_matches_skin_dominant(tmp_path):
    """渐变中段与 scrim 必须与传入 dominant 完全一致——整体色彩同源才平和。"""
    dominant = "#DDE5EE"
    layers = AssistantApp._build_middle_background(
        _tmp_png(tmp_path, "b3.png"), dominant, 0.4,
        edge_colors={"top": "#EEF2F6", "bottom": "#B9C2CC"})
    surround, _img, scrim = layers
    assert surround.gradient.colors[1] == dominant
    assert scrim.bgcolor == dominant


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
