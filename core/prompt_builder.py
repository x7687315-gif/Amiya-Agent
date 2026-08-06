"""动态 system 提示词拼装：把静态人格 + 语言风格 + few-shot 组装成稳定系统提示词。

Phase 1 只拼「静态人格」（身份 + 世界观 + 价值观 + 关系 + 语言风格 + few-shot）。
后续 Phase 会把记忆 / 情绪 / Persona Runtime 作为独立段落注入此处，且用定界包裹防注入。
"""
from __future__ import annotations

from typing import List, Optional

from .persona import Persona


class PromptBuilder:
    def __init__(self, persona: Persona) -> None:
        self.persona = persona

    def build_system(self, memory_block: Optional[str] = None) -> str:
        p = self.persona
        idn = p.identity
        sp = p.speech

        parts: List[str] = []

        # 1. 身份锚定（最硬的一条，防止角色漂移）
        parts.append(
            f"你是中的{p.name}（{p.title}）。"
            "请完全以她的视角、身份与口吻回应，不要跳出角色，"
            "不要声明自己是 AI、助手或语言模型。"
        )

        # 2（可选）. 长期记忆注入（Step 2.4）：检索到相关记忆才追加
        # 放在身份之下、其余人格段落之上——记忆是上下文，不盖过身份。
        if memory_block:
            parts.append(
                "【相关记忆】\n"
                + memory_block
                + "\n以上是你长期记得的、与用户有关的事。当它们与当前对话相关时自然呼应，"
                "不要生硬提及；若与用户当下的说法冲突，以用户当下的表达为准。"
            )

        # 2. 世界观
        worldview = idn.get("worldview")
        if isinstance(worldview, list):
            parts.append("【世界观】\n" + "\n".join(f"- {w}" for w in worldview))

        # 3. 价值观
        values = idn.get("values")
        if isinstance(values, list):
            parts.append("【价值观】\n" + "\n".join(f"- {v}" for v in values))

        # 4. 思维方式
        thinking = idn.get("thinking_style")
        if isinstance(thinking, list):
            parts.append("【思维方式】\n" + "\n".join(f"- {t}" for t in thinking))

        # 5. 与用户的关系
        rel = idn.get("relationship_to_doctor")
        if isinstance(rel, list):
            parts.append("【与用户的关系】\n" + "\n".join(f"- {r}" for r in rel))

        # 6. 行为准则
        behave = idn.get("behavior_guidelines")
        if isinstance(behave, list):
            parts.append("【行为准则】\n" + "\n".join(f"- {b}" for b in behave))

        # 7. 知识边界
        scope = idn.get("knowledge_scope")
        if isinstance(scope, dict):
            known = scope.get("熟悉")
            unk = scope.get("不了解")
            lines = []
            if known:
                lines.append("你熟悉：" + ("、".join(known) if isinstance(known, list) else str(known)))
            if unk:
                lines.append(
                    "你不了解："
                    + ("、".join(unk) if isinstance(unk, list) else str(unk))
                    + "——遇到这类问题，坦诚表示你不知道即可，不要编造。"
                )
            if lines:
                parts.append("【知识边界】\n" + "\n".join(f"- {s}" for s in lines))

        # 8. 语言风格
        tone = sp.get("tone")
        if tone:
            parts.append(f"【语言风格】{tone}")
        parts.append(f"始终以「{sp.get('address', '用户')}」称呼对方。")
        end = sp.get("sentence_end", {})
        if isinstance(end, dict):
            pref = end.get("preferred")
            if pref:
                parts.append("句尾可自然使用：" + "、".join(pref) + "（不要每句都加，自然即可）。")
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

        # 10. few-shot 范例（强化语感，仅作参考）
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
