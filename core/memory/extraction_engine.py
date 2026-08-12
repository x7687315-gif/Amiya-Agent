"""记忆抽取引擎：把"近 N 轮对话"变成候选记忆并送进人工确认闸门。

为什么需要这一层（设计意图）：
- 聊天记录 ≠ 长期记忆。对话是连续的、含噪声的、夹带临时情绪的；长期记忆
  只该保留"稳定、可复用、对用户有用"的事实。直接从对话里直写正表，会把
  "今天天气不错"固化成"用户喜欢晴天"——这是长期陪伴 Agent 最致命的污染。
- 本引擎是"AI 建议"与"长期记忆正表"之间的一道**过滤层**：它只负责把对话
  抽成候选（ExtractedMemory），并**只经 MemoryExtractor.propose_many 写
  memory_candidate 闸门**，把"是否进长期记忆"的决定权交还给人（Gate）。

三柱隔离保证：
- Memory 柱：唯一写目标，且只写 memory_candidate（绝不经过 upsert_memory/
  remember 直写正表）。
- Knowledge 柱：不读不写（knowledge/*.md 完全不参与）。
- Persona 柱：不读不写（config/persona/*、persona_state 不参与）；抽取用
  独立 system prompt，助手人格绝不泄漏进抽取 LLM。

本文件只依赖 core.memory（ExtractedMemory / MemoryExtractor）——LLMClient 仅作
类型标注，运行期不导入，确保模块的运行时依赖严格收敛在 Memory 柱内。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Dict, List

from .extractor import ExtractedMemory, MemoryExtractor
from .store import MEMORY_TYPES

if TYPE_CHECKING:  # 仅类型标注，运行期不导入 llm_client（保持依赖收敛在 core.memory）
    from ..llm_client import LLMClient

# 抽取提示词：独立于助手人格。明确"你不是助手本人"，杜绝人格泄漏。
EXTRACTION_SYSTEM_PROMPT = (
    "你是「助手的记忆整理助手」。你的唯一任务是：从最近一段对话里，"
    "抽取出【值得长期记住的、稳定的】信息。你不是助手本人，不要代入她的"
    "语气，也不要和用户对话——只输出结构化结果。\n\n"
    "抽取原则：\n"
    "1. 稳定且可复用的事实才抽：长期偏好、反复出现的习惯、重要目标、"
    "明确的身份/关系事实、已确定的计划。\n"
    "   身份(谁/年级/职业)与目标(想做什么/计划)是不同信息，必须拆成独立候选："
    "身份→fact，目标→goal，严禁合并到一条。\n"
    "2. 区分「一次性」与「持续性」：\n"
    "   - 一次性瞬时信息不要抽：今天天气、刚才某句玩笑、单次吐槽"
    "（如「今天下雨好烦」→ 不抽）。\n"
    "   - 持续性情绪/状态可记为 event 且低权重（如「最近压力特别大」→ "
    "event「近期学习压力较大」，importance 3）——便于后续共情；但绝不可"
    "写成 fact（fact 只承载客观、稳定的特质，禁止把情绪固化成"
    "「长期焦虑/性格焦虑」之类表述）。\n"
    "3. 一条候选只承载一个信息，content 用中性陈述句，忠于原意，不臆造。\n"
    "4. content 省略冗余主语「用户/用户」（上下文已明确是用户）；但 type="
    "relationship（涉及助手或他人）时必须保留明确主语，避免歧义。\n"
    "5. 候选是待人工确认的草稿，confidence 请保守，取 3–4（不要给 5）。\n"
    "6. 不要与已有记忆重复；不输出任何解释、不闲聊、不用 markdown 代码块包裹。\n\n"
    "输出格式（纯 JSON 数组，无其它内容）：\n"
    '[\n'
    '  {"type": "preference|fact|event|goal|relationship",\n'
    '   "content": "稳定的事实陈述（省去冗余主语，relationship 保留主语）",\n'
    '   "importance": 1-10,\n'
    '   "confidence": 1-5,\n'
    '   "reason": "为什么值得记（给人看）"}\n'
    "]\n"
    "无候选时输出 []。"
)


def _clamp(value: object, lo: int, hi: int) -> int:
    """把任意值夹到 [lo, hi] 整数；非法输入回退到 lo。"""
    try:
        return max(lo, min(hi, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return lo


def parse_json_array(text: str, *, max_candidates: int = 5) -> List[ExtractedMemory]:
    """把 LLM 返回的（可能不干净的）文本解析成候选记忆列表。

    设计意图（稳健解析）：LLM 输出不可信——可能带 ```json 围栏、前后废话、
    字段缺失、类型错。本函数把"脆弱的模型输出"与"后续存储"解耦：任何格式
    异常都降级为"无候选"（返回 []），而非抛错中断对话。

    步骤：
    1. 剥离 ```json 围栏，定位首个 '[' 到末个 ']'；
    2. json.loads；失败 → []；
    3. 逐条校验：type ∈ MEMORY_TYPES？content 非空且 ≤ 200 字？非法整条丢弃；
       合法 → ExtractedMemory(...)，其 __post_init__ 再做最后一道 type 校验；
    4. 截断到 max_candidates 条。
    """
    if not text:
        return []
    # 1. 去围栏、定位数组边界
    stripped = text.strip()
    if stripped.startswith("```"):
        # 去掉首行 ```json / ``` 与结尾 ```
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped[3:]
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()
    start = stripped.find("[")
    end = stripped.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    body = stripped[start : end + 1]
    # 2. 解析
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    # 3. 逐条校验 + 4. 截断
    out: List[ExtractedMemory] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        type_ = item.get("type")
        if type_ not in MEMORY_TYPES:
            continue
        content = (item.get("content") or "").strip()
        if not content or len(content) > 200:
            continue
        try:
            out.append(
                ExtractedMemory(
                    type=str(type_),
                    content=content,
                    importance=_clamp(item.get("importance", 3), 1, 10),
                    # 候选是待人工确认的草稿，置信度保守封顶 4（M4.3）：避免界面显得过于笃定
                    confidence=_clamp(item.get("confidence", 3), 1, 4),
                    reason=item.get("reason"),
                )
            )
        except (ValueError, TypeError):
            # ExtractedMemory.__post_init__ 校验失败（理论上上面已拦，双保险）
            continue
        if len(out) >= max_candidates:
            break
    return out


class ExtractionEngine:
    """把"近 N 轮对话"变成候选记忆并送进闸门。

    设计意图：本类**不持有直写正表能力**——内部只调用 MemoryExtractor.propose_many，
    物理上仍只能落 memory_candidate。依赖方向单向向下：
    ExtractionEngine → MemoryExtractor → MemoryManager.propose → store.memory_candidate。

    为什么不是 memory.upsert_memory(type, content)？
    upsert_memory 是直写正表、跳过人工确认，候选未审核即进可被检索的长期记忆，
    污染召回。引擎只 propose_many，把转正权交还给人。
    """

    def __init__(self, extractor: MemoryExtractor, llm: "LLMClient") -> None:
        self._extractor = extractor  # 写-only seam（Step A 已落地）
        self._llm = llm

    def extract(
        self,
        window: List[Dict[str, str]],
        *,
        max_candidates: int = 5,
    ) -> List[int]:
        """调抽取 LLM → 解析 → propose_many。返回新入队的候选 id 列表。

        任何异常（LLM 调用失败 / 解析失败 / 入库失败）都**静默降级**为返回 []
        ——抽取永远不能崩对话。window 为空也直接返回 []（无可抽内容）。
        """
        if not window:
            return []
        try:
            raw = self._llm.chat(
                EXTRACTION_SYSTEM_PROMPT,
                window,
                temperature=0.0,
                max_tokens=1024,
            )
        except Exception:  # noqa: BLE001 - 抽取失败绝不崩对话
            return []
        items = parse_json_array(raw, max_candidates=max_candidates)
        if not items:
            return []
        # 唯一写路径：经 MemoryExtractor → MemoryManager.propose → memory_candidate
        return self._extractor.propose_many(items)
