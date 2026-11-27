"""主动行为（P4）：启动时助手主动问候。

设计约束（用户拍板）：
- **可控 / 可关闭**：`PROACTIVE_GREETING` 配置开关（默认开），关掉即完全不主动发声。
- **可降级**：LLM 生成失败/超时 → 模板问候兜底；模板基于「当前时段 + 距上次见面天数」，
  永远能产出，绝不阻断启动或聊天。
- **范围**：仅"启动时主动问候"（不做空闲主动搭话/提醒等更激进形态）。
- **边界**：问候是 Agent 层产出的一段文本，由 UI 层负责展示；不改 Agent 的对话/记忆边界。

本模块只放**纯函数**（可独立测试、无副作用）；LLM 调用在 Agent.proactive_greeting。
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Optional


def day_segment(now: datetime) -> str:
    """把当前时刻映射到中文时段（问候用）。"""
    h = now.hour
    if 5 <= h < 9:
        return "早上"
    if 9 <= h < 12:
        return "上午"
    if 12 <= h < 14:
        return "中午"
    if 14 <= h < 18:
        return "下午"
    if 18 <= h < 23:
        return "晚上"
    return "深夜"  # 23–5


def days_since_seen(agent_state: Optional[Mapping[str, object]], now: datetime) -> Optional[int]:
    """距上次见面的整天数。无 last_seen_at / 解析失败 → None（按"近期见过"处理）。"""
    if not agent_state:
        return None
    raw = agent_state.get("last_seen_at")
    if not raw:
        return None
    try:
        last = datetime.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None
    return max(0, (now.date() - last.date()).days)


def build_greeting_context(
    agent_state: Optional[Mapping[str, object]], now: datetime
) -> dict:
    """汇总问候上下文：时段 + 距上次见面天数 + 当前情绪。"""
    return {
        "segment": day_segment(now),
        "days_since_seen": days_since_seen(agent_state, now),
        "emotion": (agent_state or {}).get("emotion", "calm"),
    }


_SEGMENT_GREETING = {
    "早上": "早上好，用户。",
    "上午": "上午好，用户。",
    "中午": "中午好，用户，吃过饭了吗？",
    "下午": "下午好，用户。",
    "晚上": "晚上好，用户，今天也辛苦了。",
    "深夜": "夜深了，用户，还没休息吗？",
}


def template_greeting(context: Mapping[str, object]) -> str:
    """确定性兜底问候：时段 + 距上次见面。永不失败（LLM 不可用时降级到此）。"""
    base = _SEGMENT_GREETING.get(str(context.get("segment", "")), "你好，用户。")
    days = context.get("days_since_seen")
    if isinstance(days, int) and days >= 2:
        return base + "好久不见——我一直在等你回来。"
    return base + "我一直在这里。"
