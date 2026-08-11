"""Knowledge 中间层：对外唯一入口，对齐 MemoryManager 的形态（Agent 只认本类）。

职责：加载语料 → 构建检索器 → 暴露 retrieve / render_block。任何初始化或检索
异常都只降级为空结果，绝不中断对话——和 Memory 的"可用性优先"原则一致。
"""
from __future__ import annotations

import logging
from typing import List, Optional

from ..memory.embedder import DEFAULT_MODEL, Embedder, get_embedder
from .loader import KnowledgeCorpus
from .retriever import KnowledgeHit, KnowledgeRetriever

log = logging.getLogger(__name__)


class KnowledgeManager:
    def __init__(
        self,
        directory,
        embedder: Embedder,
        *,
        top_k: int = 4,
    ) -> None:
        self.corpus = KnowledgeCorpus(directory)
        self.retriever = KnowledgeRetriever(self.corpus, embedder, top_k=top_k)

    @property
    def is_empty(self) -> bool:
        return len(self.corpus.chunks) == 0

    def retrieve(self, query: str, top_k: Optional[int] = None) -> List[KnowledgeHit]:
        try:
            return self.retriever.retrieve(query, top_k=top_k)
        except Exception as e:  # noqa: BLE001
            log.warning("知识检索失败：%s", e)
            return []

    def render_block(self, hits: List[KnowledgeHit]) -> str:
        return self.retriever.render_block(hits)


def build_knowledge_manager(
    directory,
    *,
    backend: str = "auto",
    model_name: str = DEFAULT_MODEL,
    device: str = "cpu",
    top_k: int = 4,
) -> Optional[KnowledgeManager]:
    """工厂：加载失败返回 None（调用方据此让知识检索静默关闭，不致命）。"""
    try:
        embedder = get_embedder(backend, model_name=model_name, device=device)
        return KnowledgeManager(directory, embedder, top_k=top_k)
    except Exception as e:  # noqa: BLE001
        log.warning("知识库初始化失败，知识检索关闭：%s", e)
        return None
