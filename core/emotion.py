"""Emotion 支柱（当前情绪）：轻量规则判定，产出可注入提示词的【当前情绪】块。

定位（见 ROADMAP §4.2 / docs/12 §5.5）：
- 只做**当前状态**、不做长期情绪模型；情绪是第三根上下文支柱（Knowledge / Memory / Emotion）。
- 规则/关键词判定（本地、零额外 LLM 调用、可解释、低资源友好）。
- 产出 4 个状态键，与 ui/theme.py 的 EMOTIONS 对齐：calm / thinking / worried / happy。

边界红线：
- 情绪状态持久化到 `persona_state.emotion`（系统状态表），**绝不写入 `memory` 表**
  （用户事实/经历）。三层物理隔离不被打破。
- 情绪只"染色"回应，不改变助手的身份与价值观（同 relationship 的"不控语气"精神）。
"""
from __future__ import annotations

EMOTION_KEYS = ("calm", "thinking", "worried", "happy")
EMOTION_LABELS = {
    "calm": "平静",
    "thinking": "思考",
    "worried": "担忧",
    "happy": "愉悦",
}
DEFAULT_EMOTION = "calm"

# 关键词表（中文为主，可后续扩充）。判定优先级：worried > happy > thinking > calm。
# 担忧优先：用户表达困扰时，助手的共情（担忧/关心）比"解题"更该先被看见。
_WORRIED = (
    "担心", "焦虑", "难过", "伤心", "委屈", "痛苦", "崩溃", "害怕", "怕", "哭",
    "失眠", "睡不着", "睡不好", "疲惫", "疲倦", "好累", "很累", "压力", "烦",
    "不舒服", "难受", "生病", "病", "加班", "deadline", "考试", "失败", "吵架",
    "孤独", "寂寞", "撑不住", "扛不住", "抑郁",
)
_HAPPY = (
    "开心", "高兴", "快乐", "谢谢", "感谢", "感激", "太好了", "太棒", "棒", "厉害",
    "哈哈", "嘿嘿", "嘻嘻", "喜欢", "爱你", "顺利", "完成", "搞定", "成功", "通关",
    "好玩", "有趣", "惊喜", "舒服", "轻松", "放假", "周末",
)
_THINKING = (
    "怎么", "为什么", "为何", "如何", "什么", "哪个", "哪些", "吗", "么", "?", "？",
    "帮我", "请教", "请问", "想想", "考虑", "分析", "计划", "建议", "怎么办", "怎样",
    "能不能", "可不可以", "应该", "要不要", "区别", "对比", "评价", "看法",
)


def detect_emotion(user_text: str) -> str:
    """根据用户本轮的话，规则判定助手当前应处的情绪状态。

     deterministic、无副作用；空文本 → calm。
    """
    text = (user_text or "").strip()
    if not text:
        return DEFAULT_EMOTION
    if any(w in text for w in _WORRIED):
        return "worried"
    if any(w in text for w in _HAPPY):
        return "happy"
    if any(w in text for w in _THINKING):
        return "thinking"
    return DEFAULT_EMOTION


def emotion_block(emotion: str) -> str:
    """把情绪状态渲染成【当前情绪】提示词块（注入位置在【与用户的关系】之后）。

    措辞保持"助手视角"的陪伴语气，只做轻度情绪染色，不控语气、不改人格。
    """
    label = EMOTION_LABELS.get(emotion, EMOTION_LABELS[DEFAULT_EMOTION])
    return (
        f"【当前情绪】此刻的你有些{label}。"
        "让这份情绪自然地融进你的回应里，但不要因此脱离你的身份、价值观和说话方式；"
        "它只是一种心情底色，不是需要向用户汇报的状态。"
    )
