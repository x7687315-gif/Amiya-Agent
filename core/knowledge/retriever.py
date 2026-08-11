"""Knowledge 检索：向量相似度 + 关键词重叠的混合打分（轻量、无额外依赖）。

与 Memory 检索（五通道：vector/keyword/recency/importance/confidence）刻意不同——
知识是**静态语料**，没有"最近更新 / 重要度 / 置信度"这些维度，所以用更简单的
两通道（vector 0.7 + keyword 0.3）即可。两者共用同一套 Embedder 原语，但语料、
打分、存储完全独立，绝不互相调用。

降级策略与 Memory 一致：嵌入不可用 / 检索异常都只降级为空结果，不阻断对话。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from ..memory.embedder import Embedder, cosine, tokenize

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnowledgeHit:
    id: str
    source: str
    heading: str
    text: str
    score: float
    channels: dict = field(default_factory=dict)


class KnowledgeRetriever:
    def __init__(
        self,
        corpus,
        embedder: Embedder,
        *,
        top_k: int = 4,
        vector_w: float = 0.7,
        keyword_w: float = 0.3,
    ) -> None:
        self.corpus = corpus
        self.embedder = embedder
        self.top_k = top_k
        self.vector_w = vector_w
        self.keyword_w = keyword_w
        self._vectors: List[List[float]] = self._embed_all()

    def _embed_all(self) -> List[List[float]]:
        if not self.corpus.chunks:
            return []
        try:
            return list(self.embedder.encode([c.text for c in self.corpus.chunks]))
        except Exception as e:  # noqa: BLE001 - 嵌入失败则知识检索整体降级
            log.warning("知识库向量化失败，知识检索降级为空：%s", e)
            return []

    def retrieve(self, query: str, top_k: Optional[int] = None) -> List[KnowledgeHit]:
        top_k = top_k or self.top_k
        if not self._vectors:
            return []
        try:
            qv = self.embedder.encode([query])[0]
        except Exception as e:  # noqa: BLE001
            log.warning("知识查询向量化失败：%s", e)
            return []

        q_tokens = set(tokenize(query))
        scored = []
        for chunk, vec in zip(self.corpus.chunks, self._vectors):
            v = cosine(qv, vec)
            if v < 0:
                v = 0.0
            k = self._keyword_overlap(q_tokens, chunk.text)
            score = self.vector_w * v + self.keyword_w * k
            scored.append((score, chunk, v, k))

        scored.sort(key=lambda x: x[0], reverse=True)
        hits: List[KnowledgeHit] = []
        for score, chunk, v, k in scored[:top_k]:
            if score <= 0:
                continue
            hits.append(
                KnowledgeHit(
                    id=chunk.id,
                    source=chunk.source,
                    heading=chunk.heading,
                    text=chunk.text,
                    score=round(score, 4),
                    channels={"vector": round(v, 3), "keyword": round(k, 3)},
                )
            )
        return hits

    @staticmethod
    def _keyword_overlap(q_tokens, text) -> float:
        if not q_tokens:
            return 0.0
        t_tokens = set(tokenize(text))
        if not t_tokens:
            return 0.0
        return len(q_tokens & t_tokens) / len(q_tokens)

    @staticmethod
    def render_block(hits: List[KnowledgeHit]) -> str:
        """把命中渲染成可注入提示词的片段（无分数、无来源噪声，只给正文）。"""
        if not hits:
            return ""
        lines = ["【角色知识】"]
        for h in hits:
            lines.append(f"- （{h.source} › {h.heading}）{h.text}")
        lines.append(
            "以上是关于助手的设定与世界观知识，当与当前对话相关时自然运用，"
            "不要逐字复述，也不要声明你「在知识库里查到」。"
        )
        return "\n".join(lines)
