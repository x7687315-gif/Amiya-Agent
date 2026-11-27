"""Knowledge 检索：向量召回 + BM25 召回，RRF 融合（2.x R1 混合检索）。

两种模式：
- **hybrid=False（默认）**：向量余弦 + 朴素关键词重叠加权——Phase 2 的旧行为，逐字节不变；
- **hybrid=True**：向量与 BM25 各自排名，用 RRF 融合。只用名次不用原始分，
  避免 BM25 与余弦的量纲差异（见 `docs/2x/IMPL_19_*`）。

为什么保留两条路：知识库是**静态语料**，没有 memory 的 recency/importance 维度；
而混合检索的收益依赖语料规模，小语料下未必更好。所以做成可回滚开关，
默认关闭，用评测数据决定是否开启。

降级策略与 Memory 一致：嵌入不可用 / 检索异常都只降级为空结果，不阻断对话。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from ..memory.embedder import Embedder, cosine, tokenize
from .bm25 import BM25Index, rrf_fuse, top_indices

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
        hybrid: bool = False,
        rrf_k: int = 60,
        pool: int = 12,
        min_dense: float = 0.15,
    ) -> None:
        self.corpus = corpus
        self.embedder = embedder
        self.top_k = top_k
        self.vector_w = vector_w
        self.keyword_w = keyword_w
        self.rrf_k = rrf_k
        self.pool = pool
        self.min_dense = min_dense
        self._vectors: List[List[float]] = self._embed_all()
        # BM25 索引只在 hybrid 模式下构建（避免默认路径多做一份无用索引）
        self._bm25: Optional[BM25Index] = (
            BM25Index([c.text for c in corpus.chunks]) if hybrid else None
        )

    @property
    def hybrid_enabled(self) -> bool:
        return self._bm25 is not None and self._bm25.available

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
        if self.hybrid_enabled:
            return self._retrieve_hybrid(query, top_k)
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

    def _retrieve_hybrid(self, query: str, top_k: int) -> List[KnowledgeHit]:
        """向量 + BM25 双路召回，RRF 融合。

        候选池 = 每路各取 `max(top_k*4, pool)`，融合后回到 top_k——
        RRF 只看名次，所以两路的名次都必须是**各自独立**算出来的，
        不能先按向量分排序再"补"关键词。
        """
        try:
            qv = self.embedder.encode([query])[0]
        except Exception as e:  # noqa: BLE001
            log.warning("知识查询向量化失败（本轮退回关键词路）：%s", e)
            qv = None

        dense: List[float] = []
        for vec in self._vectors:
            try:
                v = cosine(qv, vec) if qv is not None else 0.0
            except Exception:  # noqa: BLE001 - 单条向量异常不影响其余
                v = 0.0
            dense.append(max(0.0, v))
        keyword = self._bm25.scores(query) if self._bm25 is not None else [0.0] * len(dense)

        pool = max(top_k * 4, self.pool)
        fused = rrf_fuse(
            [top_indices(dense, pool), top_indices(keyword, pool, positive_only=True)],
            k=self.rrf_k,
        )

        hits: List[KnowledgeHit] = []
        for idx, score in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0])):
            if len(hits) >= top_k:
                break
            # 相关性下限：两路都不沾边就不塞进上下文（宁缺毋滥）
            if dense[idx] < self.min_dense and keyword[idx] <= 0:
                continue
            chunk = self.corpus.chunks[idx]
            hits.append(
                KnowledgeHit(
                    id=chunk.id,
                    source=chunk.source,
                    heading=chunk.heading,
                    text=chunk.text,
                    score=round(score, 6),  # RRF 分（量纲与余弦不同，勿混用）
                    channels={
                        "vector": round(dense[idx], 3),
                        "keyword": round(keyword[idx], 3),
                    },
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
            "以上是知识库中与当前对话相关的资料（可能是角色设定，也可能是项目文档），"
            "相关时自然运用，不要逐字复述，也不要声明你「在知识库里查到」。"
        )
        return "\n".join(lines)
