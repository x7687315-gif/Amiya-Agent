"""配置加载：从 .env 读取 DeepSeek 凭据与模型参数。

设计点（来自旧版复盘）：
- API Key 缺失时显式抛 ConfigError，绝不把故障伪装成"通讯干扰"台词。
- 所有可调参数集中于此，避免散落硬编码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - python-dotenv 可选
    pass


class ConfigError(Exception):
    """配置缺失或非法时抛出，由 UI 显示为明确错误。"""


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass
class Settings:
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout: int
    history_limit: int  # 短期记忆保留的"轮数"上限（每轮 = 1 用户 + 1 助手）
    memory_enabled: bool  # 关闭后 Agent 退回纯内存模式（Phase 1 行为）
    memory_db_path: str  # 长期记忆 SQLite 位置（data/ 已在 .gitignore）
    embedding_backend: str  # 嵌入后端：auto / local / hashing
    embedding_model: str  # 嵌入模型名（默认 bge-small-zh-v1.5，中文场景）
    embedding_device: str  # 推理设备：cpu / cuda
    memory_top_k: int  # 每轮注入提示词的最相关记忆条数
    knowledge_dir: str  # 角色知识库目录（knowledge/*.md，Knowledge RAG 语料）
    knowledge_top_k: int  # 每轮注入提示词的最相关知识片段条数
    extract_auto: bool  # 是否自动抽取候选记忆（默认 False；手动整理优先）
    default_extract_window: int  # 自动抽取窗口（轮数）；10 轮上下文足够避免碎片化
    manual_extract_window: int  # 手动「整理记忆」窗口（轮数）；更长以覆盖搁置的话题
    tts_voice: str  # 语音档案名（core/tts/voice_profiles/<name>.yaml），换声音不改代码
    tts_text_lang: str  # 朗读文本语言（zh/en/…，与 /tts 契约一致）
    tts_enabled: bool  # 语音功能总开关（TTS_ENABLED）。0 = 彻底关闭朗读（🔊 隐藏、不合成），区别于会话级静音
    ui_skin: str  # UI 皮肤："auto" 走情绪联动；否则为具体皮肤 id（starry/warm/…）。单一字段，无 ui_skin_auto。
    ui_left_width: int  # 左栏宽度（可拖拽调节，持久化；默认 260）
    ui_right_width: int  # 右栏宽度（可拖拽调节，持久化；默认 300）


def load_settings() -> Settings:
    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise ConfigError(
            "缺少 DEEPSEEK_API_KEY。请在项目根目录的 .env 中填入你的 DeepSeek API Key"
            "（可复制 .env.example 后填写）。"
        )
    return Settings(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1").rstrip("/"),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        temperature=float(os.getenv("TEMPERATURE", "0.9")),
        max_tokens=int(os.getenv("MAX_TOKENS", "1024")),
        timeout=int(os.getenv("TIMEOUT", "30")),
        history_limit=int(os.getenv("HISTORY_LIMIT", "20")),
        memory_enabled=_env_bool("MEMORY_ENABLED", True),
        memory_db_path=(os.getenv("MEMORY_DB_PATH") or "data/assistant.db").strip(),
        embedding_backend=(os.getenv("EMBEDDING_BACKEND") or "auto").strip(),
        embedding_model=(
            os.getenv("EMBEDDING_MODEL") or "BAAI/bge-small-zh-v1.5"
        ).strip(),
        embedding_device=(os.getenv("EMBEDDING_DEVICE") or "cpu").strip(),
        memory_top_k=int(os.getenv("MEMORY_TOP_K", "5")),
        knowledge_dir=(os.getenv("KNOWLEDGE_DIR") or "knowledge").strip(),
        knowledge_top_k=int(os.getenv("KNOWLEDGE_TOP_K", "4")),
        extract_auto=_env_bool("EXTRACT_AUTO", False),
        default_extract_window=int(os.getenv("DEFAULT_EXTRACT_WINDOW", "10")),
        manual_extract_window=int(os.getenv("MANUAL_EXTRACT_WINDOW", "20")),
        tts_voice=(os.getenv("TTS_VOICE") or "assistant").strip(),
        tts_text_lang=(os.getenv("TTS_TEXT_LANG") or "zh").strip(),
        tts_enabled=_env_bool("TTS_ENABLED", True),
        ui_skin=(os.getenv("UI_SKIN") or "auto").strip(),
        ui_left_width=int(os.getenv("UI_LEFT_WIDTH") or "260"),
        ui_right_width=int(os.getenv("UI_RIGHT_WIDTH") or "300"),
    )


def persist_ui_skin(skin_id: str, env_path: "os.PathLike | str | None" = None) -> None:
    """把 ``UI_SKIN=<skin_id>`` 写回 .env（不存在则创建），供皮肤手动选择持久化。

    皮肤计划 Phase 2：PersonaDrawer「外观」区块选中后调用，**重启后生效**
    （运行时换肤是 Phase 4 边界，不在本函数预支）。只改 UI_SKIN 这一行，
    其余配置原样保留。env_path 供测试注入，生产默认写项目根 .env。
    """
    path = Path(env_path) if env_path else Path(__file__).resolve().parent / ".env"
    skin_id = (skin_id or "auto").strip()
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.strip().startswith("UI_SKIN="):
            out.append(f"UI_SKIN={skin_id}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"UI_SKIN={skin_id}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def persist_ui_layout(left_width: int, right_width: int, env_path: "os.PathLike | str | None" = None) -> None:
    """把栏宽写回 .env（UI_LEFT_WIDTH / UI_RIGHT_WIDTH），其余配置原样保留。

    拖拽分栏手柄松手时调用（2026-08-17 可调聊天区需求）。env_path 供测试注入。
    """
    path = Path(env_path) if env_path else Path(__file__).resolve().parent / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    wanted = {"UI_LEFT_WIDTH": str(int(left_width)), "UI_RIGHT_WIDTH": str(int(right_width))}
    out: list[str] = []
    seen: set = set()
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in wanted:
            out.append(f"{key}={wanted[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in wanted.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text(chr(10).join(out) + chr(10), encoding="utf-8")
