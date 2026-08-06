"""记忆检索层：五通道轻量混合打分。

    score = w_v·vector + w_k·keyword + w_r·recency + w_i·importance + w_c·confidence

为什么不是纯向量：单靠语义相似度，「用户准备高考」和「用户喜欢吃辣」
在问「我最近在忙什么」时得分可能相差无几。加入 importance / confidence
让人格稳定——重要且可信的事实优先被想起；加入 recency 让"最近的事"
自然浮上来；保留 keyword 是因为专有名词（人名、项目名）恰恰是
小型嵌入模型最容易糊掉、而字面匹配最可靠的部分。

**Memory ≠ Knowledge RAG**：本层只检索「关于用户的记忆」。
外部资料（文档、网页）属于另一条链路，将来由 KnowledgeRetriever 承担，
两者绝不混表、绝不混排序——混在一起是 Agent 项目最常见的失败模式。

向量检索实现：当前对候选集做暴力余弦。个人陪伴场景记忆量在千级，
`candidate_limit=500` 下实测远低于一次 LLM 首字延迟，属于噪声级开销，
引入 sqlite-vec 的索引维护成本此刻不划算。真正需要时的替换点是
`_vector_scores()` 一个方法——其余打分逻辑不受影响。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Dict, List, Optional, Sequence

from .embedder import Embedder, cosine, tokenize
from .store import MEMORY_TYPES, MemoryStore

log = logging.getLogger(__name__)

# 时间解析失败时的中性分。理论上不会发生（时间戳均由本项目写入），
# 留作防御：宁可这条记忆在 recency 通道不加分，也不要整次检索抛异常。
_UNKNOWN_RECENCY = 0.0


@dataclass(frozen=True)
class Weights:
    """五通道权重。总和不必为 1——最终会按实际启用的通道归一化。"""

    vector: float = 0.40
    keyword: float = 0.20
    recency: float = 0.15
    importance: float = 0.15
    confidence: float = 0.10


@dataclass(frozen=True)
class RetrievalHit:
    """一条命中，带完整分项——Inspector 与未来 UI 都要能解释"为什么想起它"。"""

    id: int
    type: str
    content: str
    importance: int
    confidence: int
    score: float
    channels: Dict[str, float] = field(default_factory=dict)

    def explain(self) -> str:
        parts = " ".join(f"{k}={v:.2f}" for k, v in self.channels.items())
        return f"[{self.type}] {self.content} (score={self.score:.3f} {parts})"


@lru_cache(maxsize=2048)
def _token_set(text: str) -> frozenset:
    """记忆正文的词集缓存。

    同一批记忆会在每次检索中被反复分词，而正文极少变动，缓存命中率很高。
    返回 frozenset 而非 list：不可变，杜绝调用方改坏缓存内容。
    """
    return frozenset(tokenize(text))


def _keyword_score(query_tokens: Sequence[str], content: str) -> float:
    """查询词在记忆正文中的加权覆盖率，范围 [0, 1]。

    用「覆盖率」而不是 Jaccard：长记忆不该仅因为字数多就被稀释掉。
    二字组权重高于单字，避免「的」「了」这类高频单字刷分。
    """
    if not query_tokens:
        return 0.0
    target = _token_set(content)
    hit = total = 0.0
    for tok in query_tokens:
        w = 1.0 if len(tok) > 1 else 0.3
        total += w
        if tok in target:
            hit += w
    return hit / total if total else 0.0


def _recency_score(row: Dict[str, object], half_life_days: float) -> float:
    """指数半衰：half_life_days 天前的记忆得 0.5 分。"""
    raw = row.get("updated_at") or row.get("created_at")
    if not isinstance(raw, str) or not raw:
        return _UNKNOWN_RECENCY
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        return _UNKNOWN_RECENCY
    age_days = max(0.0, (datetime.now() - ts).total_seconds() / 86400.0)
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


def _scale(value: object, low: int, high: int) -> float:
    """把 importance(1..10) / confidence(1..5) 线性映射到 [0, 1]。"""
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if high <= low:
        return 0.0
    return max(0.0, min(1.0, (v - low) / (high - low)))


class MemoryRetriever:
    """只读检索器：不写库（除调用方显式要求的命中计数外）。"""

    def __init__(
        self,
        store: MemoryStore,
        embedder: Embedder,
        *,
        weights: Weights = Weights(),
        half_life_days: float = 45.0,
        candidate_limit: int = 500,
        min_score: float = 0.05,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._weights = weights
        self._half_life = half_life_days
        self._candidate_limit = candidate_limit
        self._min_score = min_score

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    # ----- 向量通道（sqlite-vec 的替换点就在这里） -----
    def _query_vector(self, query: str) -> Optional[List[float]]:
        """把查询编码成向量；失败则返回 None 由上层降级。

        嵌入失败的现实原因很多：权重没下载完、显存/内存瞬时不足、
        模型文件损坏。任何一种都不该让"想起往事"这件事彻底失效——
        关键词与权重通道仍然可用。
        """
        try:
            vecs = self._embedder.encode([query])
        except Exception as e:  # noqa: BLE001 - 嵌入不可用只降级，不中断检索
            log.warning("查询嵌入失败，本次检索降级为关键词模式：%s", e)
            return None
        return vecs[0] if vecs else None

    def _vector_scores(
        self, qvec: Optional[List[float]], rows: Sequence[Dict[str, object]]
    ) -> List[float]:
        """候选集暴力余弦。换 sqlite-vec / faiss 时只需替换本方法。

        负相似度截断为 0：向量空间里的"语义相反"对记忆检索没有意义，
        保留负值会让它在加权求和中反向拉低本该由其它通道决定的排序。
        """
        if qvec is None:
            return [0.0] * len(rows)
        out: List[float] = []
        for r in rows:
            vec = r.get("vector")
            if not vec:
                out.append(0.0)
                continue
            out.append(max(0.0, cosine(qvec, vec)))  # type: ignore[arg-type]
        return out

    # ----- 主入口 -----
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        types: Sequence[str] = MEMORY_TYPES,
        min_score: Optional[float] = None,
    ) -> List[RetrievalHit]:
        """按混合分返回最相关的记忆（降序）。"""
        query = (query or "").strip()
        if not query or top_k <= 0:
            return []

        model = getattr(self._embedder, "name", None)
        rows = self._store.retrieval_rows(
            types=types, limit=self._candidate_limit, model=model
        )
        if not rows:
            return []

        # 黑名单兜底：入口已拦截过一次，但用户可能在记忆写入之后才拉黑，
        # 那些历史记忆仍躺在库里。检索这层是它们进入提示词的最后关口。
        blocked = self._store.blacklist()
        if blocked:
            rows = [
                r
                for r in rows
                if not any(kw in str(r.get("content", "")) for kw in blocked)
            ]
            if not rows:
                return []

        qvec = self._query_vector(query)
        vec_scores = self._vector_scores(qvec, rows)
        # 向量通道不可用时，把它的权重按比例分给其余通道，
        # 否则所有分数会被整体压低，min_score 阈值会误杀全部结果。
        w = self._weights
        active = {
            "vector": w.vector if qvec is not None else 0.0,
            "keyword": w.keyword,
            "recency": w.recency,
            "importance": w.importance,
            "confidence": w.confidence,
        }
        total_w = sum(active.values()) or 1.0
        norm = {k: v / total_w for k, v in active.items()}

        q_tokens = tokenize(query)
        threshold = self._min_score if min_score is None else min_score

        hits: List[RetrievalHit] = []
        for row, vs in zip(rows, vec_scores):
            channels = {
                "vector": vs,
                "keyword": _keyword_score(q_tokens, str(row.get("content", ""))),
                "recency": _recency_score(row, self._half_life),
                "importance": _scale(row.get("importance"), 1, 10),
                "confidence": _scale(row.get("confidence"), 1, 5),
            }
            score = sum(norm[k] * v for k, v in channels.items())
            if score < threshold:
                continue
            hits.append(
                RetrievalHit(
                    id=int(row["id"]),  # type: ignore[arg-type]
                    type=str(row.get("type", "")),
                    content=str(row.get("content", "")),
                    importance=int(row.get("importance") or 0),  # type: ignore[arg-type]
                    confidence=int(row.get("confidence") or 0),  # type: ignore[arg-type]
                    score=score,
                    channels=channels,
                )
            )

        hits.sort(key=lambda h: (-h.score, -h.importance, h.id))
        return hits[:top_k]


def format_memory_block(hits: Sequence[RetrievalHit]) -> str:
    """把命中渲染成注入提示词的记忆段落。

    只给类型与正文，**不给分数**——把 0.83 这种数字塞进提示词，
    模型可能真把它当成需要解释的内容说出来。
    """
    if not hits:
        return ""
    label = {
        "fact": "事实",
        "preference": "偏好",
        "event": "经历",
        "goal": "目标",
        "relationship": "关系",
    }
    lines = [f"- ({label.get(h.type, h.type)}) {h.content}" for h in hits]
    return "\n".join(lines)
