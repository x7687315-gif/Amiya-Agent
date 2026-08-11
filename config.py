"""配置加载：从 .env 读取 DeepSeek 凭据与模型参数。

设计点（来自旧版复盘）：
- API Key 缺失时显式抛 ConfigError，绝不把故障伪装成"通讯干扰"台词。
- 所有可调参数集中于此，避免散落硬编码。
"""
from __future__ import annotations

import os
from dataclasses import dataclass

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
    )
