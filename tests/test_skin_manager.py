"""皮肤系统单元测试：扫描、情绪映射、24h 过期、头像回退、SkinContext 产出。

红线（皮肤系统计划 §6.4）：Skin 只属于 UI 层。这些测试只触碰 UI 层的皮肤
解析与资源回退，不涉及 Agent 行为 / LLM prompt / TTS / 记忆。
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import flet as ft
import pytest

from ui import theme
from ui.design.avatar_provider import SkinAvatarProvider, TextAvatarProvider
from ui.design.skin import (
    DEFAULT_SKINS_DIR,
    SkinManager,
    bootstrap_skin,
)
from ui.theme import Colors, EMOTIONS, c


@pytest.fixture(autouse=True)
def _restore_theme():
    """任何 apply_skin 调用后恢复默认色板，避免污染同进程的其他测试文件。"""
    yield
    theme.apply_skin(Colors())


def _write_skin(
    folder: Path,
    sid: str,
    *,
    emotion: str | None = None,
    colors: dict | None = None,
    manual: bool = False,
    with_avatar: bool = True,
    with_bg: bool = True,
) -> Path:
    d = folder / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"id": sid, "name": sid, "description": "", "is_manual_only": manual}
    if emotion:
        meta["emotion"] = emotion
    if colors:
        meta["colors"] = colors
    (d / "skin.json").write_text(json.dumps(meta), encoding="utf-8")
    if with_avatar:
        (d / "avatar.png").write_bytes(b"\x89PNG")  # 仅需存在性，不校验图像内容
    if with_bg:
        (d / "background.jpg").write_bytes(b"\xff\xd8")
    return d


def _manager(tmp_path: Path) -> SkinManager:
    _write_skin(tmp_path, "starry", emotion="calm", colors={"PRIMARY": "#111111"})
    _write_skin(tmp_path, "warm", emotion="happy")
    _write_skin(tmp_path, "winter", emotion="thinking")
    _write_skin(tmp_path, "pale", emotion="worried")
    m = SkinManager()
    m.load_skins(tmp_path)
    return m


def test_load_skins_reads_four(tmp_path):
    m = _manager(tmp_path)
    assert set(m.registry) == {"starry", "warm", "winter", "pale"}


def test_color_override_applies_known_fields_only(tmp_path):
    m = _manager(tmp_path)
    starry = m.registry["starry"]
    assert starry.colors.PRIMARY == "#111111"  # 覆盖生效
    assert starry.colors.BG == Colors().BG  # 未覆盖字段保持 light 默认


def test_emotion_mapping(tmp_path):
    m = _manager(tmp_path)
    assert m.skin_for_emotion("calm").id == "starry"
    assert m.skin_for_emotion("thinking").id == "winter"
    assert m.skin_for_emotion("worried").id == "pale"
    assert m.skin_for_emotion("happy").id == "warm"
    assert m.skin_for_emotion("unknown").id == "starry"  # 未知情绪回落默认


def test_resolve_forced_skin(tmp_path):
    m = _manager(tmp_path)
    assert m.resolve("winter").id == "winter"
    assert m.resolve("nonexistent").id == "starry"  # 未知 id 回落默认


def test_resolve_auto_emotion_expiry(tmp_path):
    m = _manager(tmp_path)
    now = time.time()
    # 新鲜情绪（1 小时前）→ 映射皮肤
    assert m.resolve("auto", emotion="worried", emotion_ts=now - 3600, now=now).id == "pale"
    # 过期情绪（3 天前）→ 回落 calm→starry
    assert m.resolve("auto", emotion="worried", emotion_ts=now - 3 * 86400, now=now).id == "starry"
    # 24h 边界内仍生效
    assert m.resolve("auto", emotion="happy", emotion_ts=now - 86399, now=now).id == "warm"
    # 无情绪 → 默认
    assert m.resolve("auto", now=now).id == "starry"


def test_skin_avatar_uses_skin_image_regardless_of_state(tmp_path):
    av = tmp_path / "avatar.png"
    av.write_bytes(b"\x89PNG")
    p = SkinAvatarProvider(str(av))
    ctrl = p.get(state_key="happy", radius=20)  # 情绪状态键不影响图片
    assert isinstance(ctrl.content, ft.Image)
    assert ctrl.content.src == str(av)


def test_skin_avatar_fallback_to_default_then_text(tmp_path):
    default = tmp_path / "default.png"
    default.write_bytes(b"\x89PNG")
    # 皮肤头像缺失 → 回退 default.png
    p = SkinAvatarProvider(str(tmp_path / "missing.png"), default_path=str(default))
    ctrl = p.get(radius=20)
    assert isinstance(ctrl.content, ft.Image)
    assert ctrl.content.src == str(default)
    # default 也缺失 → 回退文字头像
    p2 = SkinAvatarProvider(
        str(tmp_path / "missing.png"),
        default_path=str(tmp_path / "none.png"),
        fallback=TextAvatarProvider(text="阿"),
    )
    ctrl2 = p2.get(radius=20)
    assert isinstance(ctrl2.content, ft.Text)


def test_bootstrap_produces_context_without_touching_global(tmp_path):
    _write_skin(tmp_path, "starry", emotion="calm", colors={"PRIMARY": "#222222"})

    class S:
        ui_skin = "auto"

    ctx = bootstrap_skin(S(), agent_state=None, skins_folder=tmp_path, apply=False)
    assert ctx.skin.id == "starry"
    assert isinstance(ctx.avatar_provider, SkinAvatarProvider)
    assert ctx.colors.PRIMARY == "#222222"
    assert ctx.background_path.name == "background.jpg"
    # apply=False：全局单例未被污染
    assert c.PRIMARY == Colors().PRIMARY


def test_bootstrap_reads_agent_emotion_with_expiry(tmp_path):
    _write_skin(tmp_path, "starry", emotion="calm")
    _write_skin(tmp_path, "pale", emotion="worried")
    now = time.time()

    class S:
        ui_skin = "auto"

    # 新鲜 worried（last_seen_at 为 ISO 文本，1 分钟前）→ pale
    fresh = bootstrap_skin(
        S(),
        agent_state={
            "emotion": "worried",
            "last_seen_at": datetime.fromtimestamp(now - 100).isoformat(),
        },
        skins_folder=tmp_path,
        now=now,
        apply=False,
    )
    assert fresh.skin.id == "pale"
    # 过期 worried（2 天前）→ starry
    stale = bootstrap_skin(
        S(),
        agent_state={
            "emotion": "worried",
            "last_seen_at": datetime.fromtimestamp(now - 2 * 86400).isoformat(),
        },
        skins_folder=tmp_path,
        now=now,
        apply=False,
    )
    assert stale.skin.id == "starry"


def test_apply_skin_writes_singleton_and_syncs_emotions():
    theme.apply_skin(Colors(PRIMARY="#123456"))
    assert c.PRIMARY == "#123456"
    # STATE_* 未被覆盖 → 保持默认；EMOTIONS 与 c 同步
    assert EMOTIONS["calm"].color == c.STATE_CALM


def test_real_phase1_skins_present():
    """集成：真实 resources/skins 下应有 Phase 1 的 4 套，且资源齐全。"""
    m = SkinManager()
    m.load_skins(DEFAULT_SKINS_DIR)
    assert {"starry", "warm", "winter", "pale"} <= set(m.registry)
    for sid in ("starry", "warm", "winter", "pale"):
        s = m.registry[sid]
        assert s.avatar_path.is_file(), sid
        assert s.background_path.is_file(), sid
        assert 0.0 < s.scrim_opacity <= 1.0, sid
