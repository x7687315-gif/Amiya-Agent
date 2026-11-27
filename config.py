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
    deepseek_thinking: str  # 思考模式开关：off=关（陪聊低延迟，默认）/ on=开 / default=不发送该字段
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
    memory_min_vector: float  # 相关性闸门：向量余弦下限（高于它才可进入候选）
    memory_min_keyword: float  # 相关性闸门：关键词命中比例下限（向量低时的另一条证据）
    knowledge_dir: str  # 角色知识库目录（knowledge/*.md，Knowledge RAG 语料）
    knowledge_top_k: int  # 每轮注入提示词的最相关知识片段条数
    knowledge_extra_dirs: tuple  # 额外语料目录（逗号分隔），如 docs/2x：不复制、直接读原目录
    knowledge_hybrid: bool  # R1 混合检索：True=向量+BM25 经 RRF 融合；False=旧的两通道加权（默认）
    extract_auto: bool  # 是否自动抽取候选记忆（默认 False；手动整理优先）
    default_extract_window: int  # 自动抽取窗口（轮数）；10 轮上下文足够避免碎片化
    manual_extract_window: int  # 手动「整理记忆」窗口（轮数）；更长以覆盖搁置的话题
    extract_json_mode: bool  # 抽取用 JSON Output（response_format，批2 可选增强；默认关=旧路径）
    tts_voice: str  # 语音档案名（core/tts/voice_profiles/<name>.yaml），换声音不改代码
    tts_text_lang: str  # 朗读文本语言（zh/en/…，与 /tts 契约一致）
    tts_enabled: bool  # 语音功能总开关（TTS_ENABLED）。0 = 彻底关闭朗读（🔊 隐藏、不合成），区别于会话级静音
    ui_skin: str  # UI 皮肤："auto" 走情绪联动；否则为具体皮肤 id（starry/warm/…）。单一字段，无 ui_skin_auto。
    ui_left_width: int  # 左栏宽度（可拖拽调节，持久化；默认 260）
    ui_right_width: int  # 右栏宽度（可拖拽调节，持久化；默认 300）
    ui_layout: str  # 启动布局模式（P1 提交 5 起）：new=新外壳（默认，侧栏可隐藏）；legacy=旧三栏（回退）
    runtime_enabled: bool  # 2.x Hybrid 灰度总开关：False=普通聊天走快路径（默认，零行为变化）；True=模型提出工具调用时移交 Agent Runtime
    research_session_enabled: bool  # 研究会话：保存并回传服务端工具协议状态（默认开）
    research_session_ttl_hours: float  # 会话有效期（小时）
    research_session_max_chars: int  # 单会话累计字符上限，超限则重启
    research_session_auto_restart: bool  # 状态不可用时自动重搜（False → 明确 blocked）
    web_tools_enabled: bool  # 2.x Phase 4 联网总开关：False=不声明任何服务端工具（默认，零行为变化）
    web_search_enabled: bool  # 联网搜索（deepseek_web_search）；仅在总开关开启时生效
    url_reading_enabled: bool  # 读链接（deepseek_read_url）；仅在总开关开启时生效
    scheduler_enabled: bool  # 2.x Phase 7 到期任务提醒：False=不启调度线程（默认）
    scheduler_interval_s: float  # 轮询间隔（秒）；提醒精度即此值
    scheduler_autorun: bool  # 到点后是否自动交给 Runtime 处理（默认 False=只提醒，零 LLM 调用）
    skills_enabled: bool  # 2.x Phase 6 技能：False=不扫描 skills/ 目录（默认，零行为变化）
    skills_dir: str  # 技能根目录（每个技能一个子目录，内含 SKILL.md）


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
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        deepseek_thinking=os.getenv("DEEPSEEK_THINKING", "off").strip().lower(),
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
        memory_min_vector=float(os.getenv("MEMORY_MIN_VECTOR", "0.50")),
        memory_min_keyword=float(os.getenv("MEMORY_MIN_KEYWORD", "0.20")),
        knowledge_dir=(os.getenv("KNOWLEDGE_DIR") or "knowledge").strip(),
        knowledge_top_k=int(os.getenv("KNOWLEDGE_TOP_K", "4")),
        knowledge_extra_dirs=tuple(
            part.strip() for part in (os.getenv("KNOWLEDGE_EXTRA_DIRS") or "").split(",")
            if part.strip()
        ),
        knowledge_hybrid=_env_bool("KNOWLEDGE_HYBRID", False),
        extract_auto=_env_bool("EXTRACT_AUTO", False),
        default_extract_window=int(os.getenv("DEFAULT_EXTRACT_WINDOW", "10")),
        manual_extract_window=int(os.getenv("MANUAL_EXTRACT_WINDOW", "20")),
        extract_json_mode=_env_bool("EXTRACT_JSON_MODE", False),
        tts_voice=(os.getenv("TTS_VOICE") or "amiya").strip(),
        tts_text_lang=(os.getenv("TTS_TEXT_LANG") or "zh").strip(),
        tts_enabled=_env_bool("TTS_ENABLED", True),
        ui_skin=(os.getenv("UI_SKIN") or "auto").strip(),
        ui_left_width=int(os.getenv("UI_LEFT_WIDTH") or "260"),
        ui_layout=(os.getenv("UI_LAYOUT") or "new").strip(),
        ui_right_width=int(os.getenv("UI_RIGHT_WIDTH") or "300"),
        runtime_enabled=_env_bool("RUNTIME_ENABLED", False),
        research_session_enabled=_env_bool("RESEARCH_SESSION_ENABLED", True),
        research_session_ttl_hours=float(os.getenv("RESEARCH_SESSION_TTL_HOURS", "24")),
        research_session_max_chars=int(os.getenv("RESEARCH_SESSION_MAX_CHARS", "60000")),
        research_session_auto_restart=_env_bool("RESEARCH_SESSION_AUTO_RESTART", True),
        web_tools_enabled=_env_bool("WEB_TOOLS_ENABLED", False),
        web_search_enabled=_env_bool("WEB_SEARCH_ENABLED", True),
        url_reading_enabled=_env_bool("URL_READING_ENABLED", True),
        scheduler_enabled=_env_bool("SCHEDULER_ENABLED", False),
        scheduler_interval_s=float(os.getenv("SCHEDULER_INTERVAL_S", "30")),
        scheduler_autorun=_env_bool("SCHEDULER_AUTORUN", False),
        skills_enabled=_env_bool("SKILLS_ENABLED", False),
        skills_dir=(os.getenv("SKILLS_DIR") or "skills").strip(),
    )


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写文本：先写同目录临时文件，再 `os.replace` 覆盖。

    为什么不能直接 `write_text`：这里写的是 `.env`，里面有 API Key 等配置。
    直接覆盖时若进程崩溃/断电，会留下**半截文件**——配置连同 Key 一起丢失。
    `os.replace` 在同一文件系统内是原子的：最差结果是"这次没写进去"，旧内容仍完整。
    """
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        if path.exists():  # 尽量保留原文件权限（POSIX 上 .env 可能是 600）
            try:
                os.chmod(tmp, path.stat().st_mode)
            except OSError:
                pass
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:  # pragma: no cover - 清理失败不影响主流程
            pass


def persist_ui_skin(skin_id: str, env_path: "os.PathLike | str | None" = None) -> None:
    """把 ``UI_SKIN=<skin_id>`` 写回 .env（不存在则创建），供皮肤手动选择持久化。

    只改 UI_SKIN 这一行，其余配置原样保留。env_path 供测试注入，生产默认写
    项目根 .env。

    【必须同步 os.environ】load_dotenv() 只在本模块导入时执行一次；运行时
    换肤（_rebuild_ui_async → load_settings）读的是 os.getenv 而非 .env 文件。
    只写文件会让重建拿到进程启动时的过期皮肤——「点击立即生效」实际失效
    （2026-09-06 审查 CRITICAL-1 根因）。
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
    _atomic_write_text(path, "\n".join(out) + "\n")
    os.environ["UI_SKIN"] = skin_id  # 运行时重读配置的单一真源


def persist_ui_layout(left_width: int, right_width: int, env_path: "os.PathLike | str | None" = None) -> None:
    """把栏宽写回 .env（UI_LEFT_WIDTH / UI_RIGHT_WIDTH），其余配置原样保留。

    拖拽分栏手柄松手时调用（2026-08-17 可调聊天区需求）。env_path 供测试注入。

    【必须同步 os.environ】理由同 persist_ui_skin：运行时换肤重建会经
    load_settings() 重读栏宽，只写文件会让布局回退到进程启动时的旧值。
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
    _atomic_write_text(path, chr(10).join(out) + chr(10))
    for key, value in wanted.items():
        os.environ[key] = value  # 运行时重读配置的单一真源
