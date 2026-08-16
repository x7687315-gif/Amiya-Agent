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

# ---------------------------------------------------------------------------
# Phase 2 / Phase 3（2026-08-17 收尾）：磁盘 6 套皮肤 + 选肤持久化 + 抽屉外观区块
# ---------------------------------------------------------------------------


def test_real_skins_on_disk_six_total():
    """Phase 3 验收：磁盘上 6 套皮肤全部可加载；sakura/sunset 仅手动可选。"""
    m = SkinManager()
    m.load_skins()  # 默认目录 resources/skins
    ids = set(m.registry)
    assert {"starry", "warm", "winter", "pale", "sakura", "sunset"} <= ids
    assert m.registry["sakura"].is_manual_only is True
    assert m.registry["sunset"].is_manual_only is True
    # 情绪映射的 4 套不是 manual_only（auto 模式可达）
    for sid in ("starry", "warm", "winter", "pale"):
        assert m.registry[sid].is_manual_only is False


def test_persist_ui_skin_replaces_existing_line(tmp_path):
    from config import persist_ui_skin

    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=x\nUI_SKIN=auto\nTIMEOUT=30\n", encoding="utf-8")
    persist_ui_skin("sakura", env_path=env)
    text = env.read_text(encoding="utf-8")
    assert "UI_SKIN=sakura" in text
    assert "UI_SKIN=auto" not in text
    assert "DEEPSEEK_API_KEY=x" in text  # 其它行原样保留
    assert "TIMEOUT=30" in text


def test_persist_ui_skin_appends_when_missing(tmp_path):
    from config import persist_ui_skin

    env = tmp_path / ".env"
    env.write_text("DEEPSEEK_API_KEY=x\n", encoding="utf-8")
    persist_ui_skin("auto", env_path=env)
    text = env.read_text(encoding="utf-8")
    assert "UI_SKIN=auto" in text
    assert text.startswith("DEEPSEEK_API_KEY=x")  # 追加不覆盖


class _DrawerPersona:
    identity: dict = {"perspective": ["温和"], "values": ["守护"], "thinking_style": []}
    name = "助手"
    title = "本地"


def _fake_skin(sid: str, name: str) -> "Skin":
    from ui.design.skin import Skin

    return Skin(
        id=sid,
        name=name,
        description="",
        avatar_path=Path("x") / "a.png",
        background_path=Path("x") / "b.jpg",
        colors=Colors(),
    )


class _ToggleEvent:
    def __init__(self, value: bool):
        self.control = type("C", (), {"value": value})()


def test_drawer_appearance_select_and_toggle():
    from ui.components.persona_drawer import PersonaDrawer

    picked = []
    d = PersonaDrawer(
        _DrawerPersona(),
        skins=[_fake_skin("starry", "星夜"), _fake_skin("sakura", "春樱")],
        ui_skin="auto",
        current_skin_id="starry",
        on_skin_selected=picked.append,
    )
    # 点击 sakura 缩略图 → 手动锁定
    d._handle_skin_click("sakura")
    assert picked == ["sakura"]
    assert d._auto_switch.value is False  # 手动选择自动退出跟随情绪
    assert "已切换" in (d._skin_status.value or "")
    # 打开跟随情绪 → auto
    d._handle_auto_toggle(_ToggleEvent(True))
    assert picked[-1] == "auto"
    # 关闭跟随情绪 → 锁定当前解析皮肤
    d._handle_auto_toggle(_ToggleEvent(False))
    assert picked[-1] == "starry"


def test_drawer_without_skins_hides_appearance():
    from ui.components.persona_drawer import PersonaDrawer

    d = PersonaDrawer(_DrawerPersona())  # 未注入 skins
    assert d._auto_switch is None
    for ctrl in d.controls:
        content = getattr(ctrl, "content", None)
        if hasattr(content, "controls"):
            for sub in content.controls:
                assert getattr(sub, "value", None) != "外观"


def test_bootstrap_context_carries_skins_for_drawer(tmp_path):
    """SkinContext.skins 把注册表带给 PersonaDrawer（Phase 2 接线依赖）。"""
    m = SkinManager()
    m.load_skins()
    ctx = m.bootstrap("starry", apply=False)
    ids = {s.id for s in ctx.skins}
    assert {"starry", "sakura", "sunset"} <= ids


# ---------------------------------------------------------------------------
# 统一底色契约 + 左下角显式切肤入口（2026-08-17 用户需求）
# ---------------------------------------------------------------------------


def test_unified_wallpaper_background_contract():
    """主界面面板一律透明（bgcolor=None）：壁纸+遮罩是全局统一底色。

    用户明确要求：左右上下的底色与聊天框一致，不留漏图的空隙。
    该契约冻结此设计——新增面板若需要底色，应做成内容卡片而非整栏底色。
    """
    from ui.components.chat_area import ChatArea
    from ui.components.date_nav import DateNav
    from ui.components.header import Header
    from ui.components.input_bar import InputBar
    from ui.components.persona_status import PersonaStatusPanel

    class _P:
        name = "助手"
        title = "本地"
        identity: dict = {}
        address = None

    assert ChatArea(_P).bgcolor is None
    assert DateNav(on_day_change=lambda _d: None, today="2026-08-17").bgcolor is None
    assert Header(_P).bgcolor is None
    assert InputBar(on_send=lambda _t: None).bgcolor is None
    assert PersonaStatusPanel(_P).bgcolor is None


def test_status_panel_skin_entry_visible_and_interactive(tmp_path):
    """左下角「更换皮肤」入口：注入 skins 即出现，可展开、可选肤、可切跟随情绪。"""
    from ui.components.persona_status import PersonaStatusPanel

    picked = []
    panel = PersonaStatusPanel(
        _DrawerPersona(),
        skins=[_fake_skin("starry", "星夜"), _fake_skin("sakura", "春樱")],
        ui_skin="auto",
        current_skin_id="starry",
        on_skin_selected=picked.append,
    )
    assert panel._skin_toggle_btn is not None  # 显式入口存在
    assert panel._skin_panel is not None and panel._skin_panel.visible is False  # 默认收起

    panel._toggle_skin_panel()  # 展开
    assert panel._skin_panel.visible is True
    panel._toggle_skin_panel()  # 再点收起
    assert panel._skin_panel.visible is False

    panel._handle_skin_click("sakura")
    assert picked == ["sakura"]
    assert panel._auto_switch.value is False
    assert "已切换" in (panel._skin_status.value or "")

    panel._handle_auto_toggle(_ToggleEvent(True))
    assert picked[-1] == "auto"
    panel._handle_auto_toggle(_ToggleEvent(False))
    assert picked[-1] == "starry"  # 关闭跟随 = 锁定当前


def test_status_panel_without_skins_keeps_old_behavior():
    """未注入 skins：无入口（旧测试/旧调用方零影响）。"""
    from ui.components.persona_status import PersonaStatusPanel

    panel = PersonaStatusPanel(_DrawerPersona())
    assert panel._skin_toggle_btn is None
    assert panel._skin_panel is None


# ---------------------------------------------------------------------------
# 即时换肤（2026-08-17 用户要求：点击立即生效）+ 玻璃输入框
# ---------------------------------------------------------------------------


def test_input_field_uses_glass_background():
    """底部输入框用半透明玻璃底（透出壁纸），不再形成白色横条。"""
    from ui.components.input_bar import InputBar

    bar = InputBar(on_send=lambda _t: None)
    assert bar._field.bgcolor == c.SURFACE_GLASS
    assert c.SURFACE_GLASS.endswith("F5F7FC") and len(c.SURFACE_GLASS) == 9  # #AARRGGBB


def test_on_skin_selected_persists_and_rebuilds(monkeypatch):
    """选肤回调：写 .env + 触发运行时重建（AssistantApp 级，注入替身）。"""
    import ui.app as app_mod
    from ui.app import AssistantApp

    saved = []
    rebuilt = []
    monkeypatch.setattr(app_mod, "persist_ui_skin", lambda sid: saved.append(sid))
    app = AssistantApp()
    app.input_bar = None  # 非 loading
    app._apply_skin_runtime = lambda: rebuilt.append(True)
    app._on_skin_selected("sakura")
    assert saved == ["sakura"]
    assert rebuilt == [True]


def test_on_skin_selected_skips_rebuild_while_generating(monkeypatch):
    """正在生成回复时只保存、不重建（流式 worker 持有旧控件）。"""
    import ui.app as app_mod
    from ui.app import AssistantApp

    saved = []
    rebuilt = []
    monkeypatch.setattr(app_mod, "persist_ui_skin", lambda sid: saved.append(sid))
    app = AssistantApp()

    class _BusyBar:
        _is_loading = True

    app.input_bar = _BusyBar()
    app._apply_skin_runtime = lambda: rebuilt.append(True)
    app._on_skin_selected("warm")
    assert saved == ["warm"]
    assert rebuilt == []  # 生成中：只保存不重建


def test_apply_skin_runtime_requires_page_and_persona():
    from ui.app import AssistantApp

    app = AssistantApp()  # page/persona 均为 None
    app._apply_skin_runtime()  # 应静默返回，不抛异常
    assert app._rebuilding is False  # 未调度不置位


def test_apply_skin_runtime_defers_to_event_loop():
    """重建必须延迟到事件循环（同步 page.clean 会话会被回收——实测 bug）。"""
    from ui.app import AssistantApp

    scheduled = []

    class _FakePage:
        def run_task(self, coro_fn, *a):
            scheduled.append(coro_fn)

    app = AssistantApp()
    app.page = _FakePage()
    app._persona = object()
    app._apply_skin_runtime()
    assert app._rebuilding is True  # 已置位（防重入）
    assert len(scheduled) == 1  # 只调度，不同步重建
    app._apply_skin_runtime()  # 重建中再点：被防重入挡掉
    assert len(scheduled) == 1
    # 手动跑协程验证可完成（page 为 fake，build 流程会走异常兜底分支但不抛）
    import asyncio

    asyncio.run(scheduled[0]())
    assert app._rebuilding is False  # 完成后复位
