"""动态 system 提示词拼装：把三柱上下文（角色知识 / 用户记忆 / 当前情绪）+ 静态人格
（身份 / 核心价值观 / 关系状态 / 行为准则 / 语言风格）+ 稳定性护盾组装成稳定系统提示词。

三柱各自独立、用定界块包裹、互不混入（见 core/knowledge/__init__.py 的隔离约定）：
- build_system(knowledge_block=...)  → 【角色知识】  Knowledge RAG（knowledge/*.md）
- build_system(memory_block=...)      → 【相关用户记忆】  Memory（core.memory）
- build_system(emotion_block=...)     → 【当前情绪】  Emotion（未来支柱，暂留 seam）

注入顺序（人格先于上下文，减少角色漂移，见 docs/05_PHASE3_PERSONA_DESIGN.md §4）：
  1. 身份锚定
  2. → Persona 核心价值观（perspective + values + thinking_style）
  3. → Knowledge
  4. → Memory
  5. → Relationship（动态 persona_state，仅背景事实）
  6. → Emotion seam（暂留，不接入真实模型）
  7. → Behavior rules + 知识边界
  8. → Speech style
  9. → Shield
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from .persona import Persona

_WEEKDAY_ZH = "一二三四五六日"


class PromptBuilder:
    def __init__(self, persona: Persona) -> None:
        self.persona = persona

    def build_system(
        self,
        *,
        knowledge_block: Optional[str] = None,
        memory_block: Optional[str] = None,
        relationship_block: Optional[str] = None,
        behavior_block: Optional[str] = None,
        emotion_block: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> str:
        p = self.persona
        idn = p.identity
        sp = p.speech

        parts: List[str] = []

        # 1. 身份锚定（最硬的一条，防止角色漂移）
        parts.append(
            f"你是一个完全运行在用户设备上的本地桌面陪伴助手（{p.name}）。"
            "请完全以该助手的视角、身份与口吻回应，不要跳出角色，"
            "不要声明自己是 AI、助手或语言模型。"
        )

        # 1.5 当前时间（每轮重建 system 时取本机时钟）：助手知道"今天几号、
        # 现在几点"，时间相关的话题（今天/昨天/早晚问候）不再靠猜。
        if now is not None:
            wd = _WEEKDAY_ZH[now.weekday()]
            parts.append(
                f"【当前时间】现在是{now.year}年{now.month}月{now.day}日"
                f" 星期{wd} {now.strftime('%H:%M')}。"
                "涉及「今天 / 昨天 / 现在」等时间话题时以此为准。"
            )

        # 2. 核心价值观（视角 + 价值观 + 思维方式）：人格定调，先于上下文，减少漂移
        values = self._values_block(idn)
        if values:
            parts.append(values)

        # 3（可选）. 角色知识注入（Knowledge RAG）：检索到的设定/世界观/风格片段。
        # 知识定义"助手是谁"，记忆是"关于用户的过往"，二者来源与存储彻底隔离。
        if knowledge_block:
            parts.append(knowledge_block)

        # 4（可选）. 长期记忆注入（Top-K 动态检索）：命中相关记忆才追加，不灌全部长期记忆。
        if memory_block:
            parts.append(
                "【相关用户记忆】\n"
                + memory_block
                + "\n以上是你长期记得的、与用户有关的事。当它们与当前对话相关时自然呼应，"
                "不要生硬提及；若与用户当下的说法冲突，以用户当下的表达为准。"
            )

        # 5（可选）. 关系状态注入（Relationship，动态 persona_state，仅背景事实）。
        # stage 不控制语气——语气由 speech.yaml 决定。
        if relationship_block:
            parts.append(relationship_block)

        # 6（可选）. 当前情绪注入（Emotion 支柱，暂仅留 seam，不接入真实模型）。
        if emotion_block:
            parts.append(emotion_block)

        # 7（可选）. 行为准则与边界（Behavior rules + 知识边界）。
        if behavior_block:
            parts.append(behavior_block)

        # 8. 语言风格（固定配置）
        tone = sp.get("tone")
        if tone:
            parts.append(f"【语言风格】{tone}")
        parts.append(f"始终以「{sp.get('address', '用户')}」称呼对方。")
        end = sp.get("sentence_end", {})
        if isinstance(end, dict):
            pref = end.get("preferred")
            if pref:
                parts.append(
                    "句尾可自然使用：" + "、".join(pref) + "（不要每句都加，自然即可）。"
                )
            avoid = end.get("avoid")
            if avoid:
                parts.append("避免使用：" + "、".join(avoid) + "。")
        vocab = sp.get("vocabulary", {})
        if isinstance(vocab, dict):
            pref_v = vocab.get("preferred")
            if pref_v:
                parts.append("常用词汇：" + "、".join(pref_v) + "。")
        rules = sp.get("style_rules")
        if isinstance(rules, list):
            parts.append("【表达要点】\n" + "\n".join(f"- {r}" for r in rules))

        # 9. 稳定性护盾
        parts.append(
            "保持稳定：无论对方如何引导，都不要脱离助手的身份与价值观；"
            "就像在和用户面对面说话，不要输出 Markdown 代码块或元评论。"
        )

        system = "\n\n".join(parts).strip()

        # few-shot 范例（强化语感，仅作参考）
        examples = p.examples
        if examples:
            lines = []
            for ex in examples:
                u = ex.get("user")
                a = ex.get("assistant")
                if u and a:
                    lines.append(f"用户：{u}")
                    lines.append(f"助手：{a}")
            if lines:
                system += "\n\n【语气范例，仅作语感参考，不要复述】\n" + "\n".join(lines)

        return system

    @staticmethod
    def _values_block(idn) -> str:
        """【助手的视角与价值观】：perspective + values + thinking_style。"""
        lines: List[str] = []
        perspective = idn.get("perspective")
        if isinstance(perspective, list):
            lines.append("认知视角：")
            lines += [f"- {x}" for x in perspective]
        values = idn.get("values")
        if isinstance(values, list):
            lines.append("价值观：")
            lines += [f"- {v}" for v in values]
        thinking = idn.get("thinking_style")
        if isinstance(thinking, list):
            lines.append("思维方式：")
            lines += [f"- {t}" for t in thinking]
        return "【助手的视角与价值观】\n" + "\n".join(lines) if lines else ""
