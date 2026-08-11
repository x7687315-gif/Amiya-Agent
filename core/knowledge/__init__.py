"""角色知识库（Knowledge RAG）—— 三柱隔离架构中的「角色设定」支柱。

设计边界（冻结文档新增 §4「三层上下文，互不混入」）：

    角色设定  ──►  Knowledge RAG   ← 本包
    用户经历  ──►  Memory         ← core.memory（SQLite）
    当前情绪  ──►  Emotion        ← 未来支柱

关键约束：
- 本包**只读** knowledge/ 目录下的 Markdown 静态语料，**绝不**写入 memory 表，
  也**绝不**从 memory 表读取——角色知识是"设定"，用户记忆是"经历"，两者物理隔离。
- 本包只复用 core.memory.embedder 的「嵌入原语」（Embedder / cosine / tokenize），
  那是通用 tokenizer/向量工具，不是记忆数据。复用同一个嵌入模型对两类语料都更一致。
- 检索结果以独立定界块【角色知识】注入提示词，与【相关用户记忆】并列、不混。

典型用法：
    from core.knowledge import build_knowledge_manager
    km = build_knowledge_manager("knowledge", backend="auto")
    hits = km.retrieve("助手和用户是什么关系？", top_k=3)
    block = km.render_block(hits)   # 交给 PromptBuilder.build_system(knowledge_block=block)
"""
from __future__ import annotations

from .loader import KnowledgeChunk, KnowledgeCorpus
from .manager import KnowledgeManager, build_knowledge_manager
from .retriever import KnowledgeHit, KnowledgeRetriever

__all__ = [
    "KnowledgeCorpus",
    "KnowledgeChunk",
    "KnowledgeRetriever",
    "KnowledgeHit",
    "KnowledgeManager",
    "build_knowledge_manager",
]
