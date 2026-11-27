"""R1 混合检索测试：分词、标识符保护、RRF、hybrid 检索行为。

设计前提：默认路径（hybrid=False）必须与旧行为逐字节等价；
hybrid=True 只在开关打开时改变排序。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from core.knowledge.bm25 import BM25Index, rrf_fuse, tokenize, top_indices
from core.knowledge.loader import KnowledgeCorpus
from core.knowledge.retriever import KnowledgeRetriever


# ---------------------------------------------------------------- 分词

def test_tokenize_keeps_identifiers_whole():
    toks = tokenize("mini-plane 的 bge-small-zh-v1.5 模型，ORD-202611030001")
    joined = " ".join(toks)
    assert "mini-plane" in joined          # 连字符型号不被拆
    assert "bge-small-zh-v1.5" in joined   # 版本号整体保留
    assert "ord-202611030001" in joined    # 编号整体保留（小写归一）


def test_tokenize_drops_stopwords_and_punctuation():
    toks = tokenize("这是一个关于记忆的说明。")
    assert "。" not in toks and "，" not in toks
    assert "的" not in toks and "是" not in toks


def test_tokenize_empty():
    assert tokenize("") == []
    assert tokenize("，。！") == []


# ---------------------------------------------------------------- BM25

def test_bm25_ranks_matching_doc_first():
    idx = BM25Index(["湛江明天有雷阵雨", "本地的天灾预警系统", "记忆检索的五通道加权"])
    scores = idx.scores("湛江 天气")
    assert scores[0] > scores[1] and scores[0] > scores[2]


def test_bm25_empty_query_is_all_zero():
    idx = BM25Index(["文档一", "文档二"])
    assert idx.scores("") == [0.0, 0.0]


def test_bm25_empty_corpus_is_unavailable():
    idx = BM25Index([])
    assert idx.scores("任意") == []


def test_top_indices_positive_only_filters_zero():
    assert top_indices([0.0, 2.0, 0.0, 1.0], 10, positive_only=True) == [1, 3]
    assert top_indices([0.0, 2.0, 0.0, 1.0], 2) == [1, 3]


# ---------------------------------------------------------------- RRF

def test_rrf_fuse_rewards_agreement():
    fused = rrf_fuse([[1, 2, 3], [2, 1, 4]], k=60)
    # 1 与 2 两路都在前列；2 在两路中分别是 2、1 名
    assert fused[2] > fused[3]
    assert fused[1] > fused[4]


def test_rrf_union_covers_both_lists():
    fused = rrf_fuse([[0], [9]], k=60)
    assert set(fused) == {0, 9}


# ---------------------------------------------------------------- 检索器

class _TieEmbedder:
    """所有文本映射到同一向量 → 余弦全相等（用于测 channels/score 形态）。"""

    name = "tie"
    dim = 2

    def encode(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _MisleadingEmbedder:
    """故意让向量路选错：查询含「记忆」→ 与 a.md 相似；而答案在 b.md（含标识符）。

    用来证明：向量召回错的时候，关键词路能把它纠回来（这正是混合检索的意义）。
    """

    name = "misleading"
    dim = 2

    def encode(self, texts):
        out = []
        for t in texts:
            out.append([1.0, 0.0] if "记忆" in t else [0.0, 1.0])
        return out


def _corpus(tmp_path: Path) -> KnowledgeCorpus:
    (tmp_path / "a.md").write_text(
        "# 记忆\n## 检索\n记忆检索用五通道加权打分。\n", encoding="utf-8")
    (tmp_path / "b.md").write_text(
        "# 模型\n## 嵌入\nbge-small-zh-v1.5 是 512 维中文嵌入模型。\n", encoding="utf-8")
    return KnowledgeCorpus(tmp_path)


def test_bm25_identifier_beats_filler_text():
    """精确标识符（编号/型号）在关键词路上应当压过无关文本。

    注意：这条**只测 BM25 自身**。整链路上中英混排时，bigram 分词会给常见
    中文二元组很高分，可能盖过稀有标识符——这正是需要 jieba 的原因，
    评测数据见 docs/2x/IMPL_19（bigram 下 R@5 无提升）。
    """
    idx = BM25Index([f"填充文档{i}，内容与查询无关" for i in range(5)] + ["QRX-9999 是专用编号"])
    scores = idx.scores("QRX-9999")
    assert scores[-1] > 0
    assert max(scores[:-1]) == 0


def test_hybrid_returns_channels_and_rrf_score(tmp_path):
    corpus = _corpus(tmp_path)
    hybrid = KnowledgeRetriever(corpus, _TieEmbedder(), top_k=2, hybrid=True)
    hit = hybrid.retrieve("记忆 五通道")[0]
    assert set(hit.channels) == {"vector", "keyword"}
    assert 0 < hit.score < 1          # RRF 分：Σ1/(k+rank)


def test_default_mode_is_unchanged(tmp_path):
    """默认 hybrid=False 时不构建 BM25 索引，仍走「向量 + 朴素关键词」两通道。"""
    corpus = _corpus(tmp_path)
    dense = KnowledgeRetriever(corpus, _TieEmbedder(), top_k=2)
    assert dense.hybrid_enabled is False
    hits = dense.retrieve("bge-small-zh-v1.5")
    assert hits and set(hits[0].channels) == {"vector", "keyword"}


# ---------------------------------------------------------------- 附加目录

def test_extra_dirs_are_loaded_with_prefix(tmp_path):
    main = tmp_path / "knowledge"
    main.mkdir()
    (main / "role.md").write_text("# 角色\n设定\n", encoding="utf-8")
    extra = tmp_path / "2x"
    extra.mkdir()
    (extra / "arch.md").write_text("# 架构\n六层\n", encoding="utf-8")

    corpus = KnowledgeCorpus(main, extra_dirs=(extra,))
    sources = {c.source for c in corpus.chunks}
    assert "role.md" in sources
    assert "2x/arch.md" in sources


def test_missing_extra_dir_is_ignored(tmp_path):
    main = tmp_path / "knowledge"
    main.mkdir()
    (main / "role.md").write_text("# 角色\n设定\n", encoding="utf-8")
    corpus = KnowledgeCorpus(main, extra_dirs=(tmp_path / "nope",))
    assert [c.source for c in corpus.chunks] == ["role.md"]
