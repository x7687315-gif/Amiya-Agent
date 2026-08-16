"""皮肤系统：把一张助手图片变成完整的"场景"（头像 + 壁纸 + 主题色板）。

架构红线（皮肤系统计划 §6.4）：**Skin 只属于 UI 层，不影响 Agent 行为。**
- 单向：``Agent Emotion → SkinManager.skin_for_emotion() → Skin Profile``。
- Skin 绝不反向影响 Agent 回复方式 / LLM prompt / TTS / 记忆抽取。
- Agent 不知道 Skin 的存在。

无 DynamicColors（计划 §4）：启动时确定 Skin → 物化 ``Colors`` →
``theme.apply_skin`` 在构造 UI 之前一次性写入单例 ``c``。``c`` 不是代理。

配置（计划 §6.2）：单一 ``ui_skin`` 字段——``"auto"`` 走情绪联动，
其余取值为具体皮肤 id（强制）。不再有 ``ui_skin_auto``。
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Optional

from ui.design.avatar_provider import (
    AvatarProvider,
    SkinAvatarProvider,
    TextAvatarProvider,
)
from ui.theme import Colors, apply_skin, c

# 项目根（ui/design/skin.py → parents[2] = 仓库根）
_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SKINS_DIR = _ROOT / "resources" / "skins"
DEFAULT_AVATAR_PATH = _ROOT / "resources" / "avatar" / "default.png"

DEFAULT_SKIN_ID = "starry"

# 情绪 → 皮肤 静态映射（sakura / sunset 为手动皮肤，不在此表）。
# 想调整映射，改这里或各 skin.json 的 emotion 字段即可，不动核心逻辑。
EMOTION_TO_SKIN = {
    "calm": "starry",
    "thinking": "winter",
    "worried": "pale",
    "happy": "warm",
}

# 情绪超过该时长视为失效（计划 §6.3）：长时间未用 → 回落 calm→starry，
# 避免"三天前 worried，今天一打开就是忧郁皮肤"。
EMOTION_EXPIRY_SECONDS = 24 * 3600


@dataclass(frozen=True)
class Skin:
    """一套皮肤的完整描述（头像 + 壁纸同源，色板为物化的 Colors）。"""

    id: str
    name: str
    description: str
    avatar_path: Path
    background_path: Path
    colors: Colors
    scrim_opacity: float = 0.8  # 壁纸上的浅色遮罩不透明度（保证前景可读）
    emotion: Optional[str] = None  # 仅信息性：默认映射情绪
    is_manual_only: bool = False  # True = 仅手动可选（sakura / sunset）


@dataclass(frozen=True)
class SkinContext:
    """启动时一次性产出的"已解析皮肤包"。app.py 只消费它，不自行扫描/解析。"""

    skin: Skin
    colors: Colors
    avatar_provider: AvatarProvider
    background_path: Path
    scrim_opacity: float


def _build_colors(overrides: Mapping[str, str]) -> Colors:
    """基于当前 light 默认 Colors()，用 skin.json 的 colors 覆盖已知字段。"""
    valid = {
        k: v for k, v in overrides.items() if k in Colors.__dataclass_fields__
    }
    return replace(Colors(), **valid)


def _fallback_skin() -> Skin:
    """resources/skins 为空/缺失时的兜底皮肤：默认色板 + 默认头像。"""
    return Skin(
        id=DEFAULT_SKIN_ID,
        name="星夜静谧",
        description="默认皮肤（resources/skins 缺失时的兜底）",
        avatar_path=DEFAULT_AVATAR_PATH,
        background_path=DEFAULT_AVATAR_PATH,  # 无壁纸时退化为默认图（app 侧判存在性）
        colors=Colors(),
        scrim_opacity=0.8,
        emotion="calm",
        is_manual_only=False,
    )


class SkinManager:
    """皮肤注册表 + 解析（含 24h 情绪过期）。不持有运行时可变状态。"""

    def __init__(self) -> None:
        self.registry: dict[str, Skin] = {}

    def load_skins(self, folder: "os.PathLike | str" = DEFAULT_SKINS_DIR) -> None:
        """扫描 folder 下每个子目录的 skin.json，构建 Skin 注册表。

        单个皮肤损坏只跳过并记日志，不拖垮整体（缺资源时由兜底逻辑接管）。
        """
        self.registry.clear()
        folder = Path(folder)
        if not folder.is_dir():
            return
        for sub in sorted(folder.iterdir()):
            meta = sub / "skin.json"
            if not sub.is_dir() or not meta.is_file():
                continue
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
                sid = raw.get("id") or sub.name
                self.registry[sid] = Skin(
                    id=sid,
                    name=raw.get("name", sid),
                    description=raw.get("description", ""),
                    avatar_path=sub / "avatar.png",
                    background_path=sub / "background.jpg",
                    colors=_build_colors(raw.get("colors", {})),
                    scrim_opacity=float(raw.get("scrim_opacity", 0.8)),
                    emotion=raw.get("emotion"),
                    is_manual_only=bool(raw.get("is_manual_only", False)),
                )
            except Exception:  # noqa: BLE001 - 单皮肤损坏只跳过
                continue

    def _default(self) -> Skin:
        return self.registry.get(DEFAULT_SKIN_ID) or (
            next(iter(self.registry.values())) if self.registry else _fallback_skin()
        )

    def skin_for_emotion(self, emotion: Optional[str]) -> Skin:
        """静态映射：emotion → 皮肤。未知情绪回落默认皮肤。"""
        sid = EMOTION_TO_SKIN.get(emotion or "", DEFAULT_SKIN_ID)
        return self.registry.get(sid) or self._default()

    def resolve(
        self,
        ui_skin: str,
        emotion: Optional[str] = None,
        emotion_ts: Optional[float] = None,
        now: Optional[float] = None,
    ) -> Skin:
        """决定当前皮肤。

        - ``ui_skin != "auto"``：强制对应皮肤（未知 id 回落默认）。
        - ``ui_skin == "auto"``：走情绪联动 + 24h 过期。
          情绪缺失或过期 → 回落 calm → starry。
        """
        now = time.time() if now is None else now
        if ui_skin and ui_skin != "auto":
            return self.registry.get(ui_skin) or self._default()
        # auto：情绪联动
        if emotion and emotion_ts is not None:
            try:
                fresh = (now - float(emotion_ts)) <= EMOTION_EXPIRY_SECONDS
            except (TypeError, ValueError):
                fresh = False
            if fresh:
                return self.skin_for_emotion(emotion)
        return self._default()

    def bootstrap(
        self,
        ui_skin: str,
        agent_state: Optional[Mapping[str, object]] = None,
        now: Optional[float] = None,
        apply: bool = True,
    ) -> SkinContext:
        """解析皮肤并产出 SkinContext（含一次性 apply_skin 与 SkinAvatarProvider）。

        ``agent_state`` 为 Agent 层持久化的状态映射（含 last_emotion /
        last_emotion_at）；Skin 只读取它，绝不写回（红线：单向）。
        ``apply=False`` 供测试使用（避免污染全局单例 c）。
        """
        emotion = None
        emotion_ts = None
        if agent_state:
            emotion = agent_state.get("last_emotion")  # type: ignore[assignment]
            emotion_ts = agent_state.get("last_emotion_at")  # type: ignore[assignment]
        skin = self.resolve(ui_skin, emotion=emotion, emotion_ts=emotion_ts, now=now)
        if apply:
            # 一次性写入单例 c——必须在构造任何 UI 之前（计划 §4 / §7）。
            apply_skin(skin.colors)
        avatar_provider = SkinAvatarProvider(
            str(skin.avatar_path),
            default_path=str(DEFAULT_AVATAR_PATH),
            fallback=TextAvatarProvider(text="阿", bgcolor=c.PRIMARY, text_color=c.ON_PRIMARY),
        )
        return SkinContext(
            skin=skin,
            colors=skin.colors,
            avatar_provider=avatar_provider,
            background_path=skin.background_path,
            scrim_opacity=skin.scrim_opacity,
        )


def bootstrap_skin(
    settings,
    agent_state: Optional[Mapping[str, object]] = None,
    skins_folder: "os.PathLike | str" = DEFAULT_SKINS_DIR,
    now: Optional[float] = None,
    apply: bool = True,
) -> SkinContext:
    """bootstrap/AppContext 层入口：扫描皮肤 → 解析 → 物化 → 产出 SkinContext。

    app.py 只调用这一个函数并消费返回的 SkinContext；不自行扫描皮肤、
    不解析情绪、不做 apply_skin。皮肤初始化归属 bootstrap 层（计划 §7）。
    """
    manager = SkinManager()
    manager.load_skins(skins_folder)
    return manager.bootstrap(
        getattr(settings, "ui_skin", "auto"),
        agent_state=agent_state,
        now=now,
        apply=apply,
    )
