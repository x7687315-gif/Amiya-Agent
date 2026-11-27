"""BM25 关键词路 + RRF 融合（Knowledge 混合检索，2.x R1）。

为什么单独一个模块：BM25 与 RRF 是**纯算法**，不依赖数据库/嵌入/UI，
可以完全离线单测（分词行为、标识符保护、融合顺序）。检索器只负责编排。

分词策略（两档，自动选择）：
1. **jieba**（若已安装）：中文分词质量最好，实测精确召回显著优于单字；
2. **字符二元组 + 标识符保护**（兜底）：无第三方依赖。
   中文按 bigram 建索引——比单字强得多（单字会让 BM25 退化成字频统计），
   代价是索引略大；对几百个 chunk 的规模完全可接受。

标识符保护（两档共用）：`mini-plane`、`bge-small-zh-v1.5`、`ORD-202611030001`、
`GPT-SoVITS` 这类 token 先正则整体提取，**绝不交给中文分词器拆碎**——
项目名/型号/编号恰恰是最需要精确匹配的内容。
"""
from __future__ import annotations

import logging
import re
import unicodedata
from typing import Dict, List, Sequence

log = logging.getLogger(__name__)

try:  # 可选依赖：缺失时退化为 bigram 分词
    import jieba as _jieba
except Exception:  # noqa: BLE001 - 任何导入失败都当"没装"
    _jieba = None

try:
    from rank_bm25 import BM25Okapi as _BM25Okapi
except Exception:  # noqa: BLE001
    _BM25Okapi = None

# 英文/数字标识符（含 . _ - / +）与连续中文，两类分开处理
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-/+]*|\d+(?:\.\d+)*%?|[\u4e00-\u9fff]+")
_TRAILING = ".,;:!?)]}、，。；：！？）】》"

# 极简停用词：只去掉高频虚词（不追求完整表，避免误杀专名）
_STOP = frozenset({
    "的", "了", "是", "在", "和", "与", "及", "或", "我", "你", "他", "她", "它",
    "这", "那", "就", "都", "而", "着", "呢", "吗", "吧", "啊", "把", "被", "让",
    "给", "对", "从", "到", "为", "以", "之", "其", "也", "很", "有", "不", "一个",
    "a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are",
})


def _normalize(text: str) -> str:
    """全角转半角 + 小写（NFKC 顺带统一全角字母/数字）。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def _cjk_tokens(run: str) -> List[str]:
    """中文片段分词：优先 jieba，否则字符二元组。"""
    if _jieba is not None:
        try:
            return [w.strip() for w in _jieba.cut(run) if w.strip()]
        except Exception as e:  # noqa: BLE001 - 分词器异常时降级到 bigram
            log.warning("jieba 分词失败，本次降级为 bigram：%s", e)
    if len(run) <= 2:
        return [run]
    return [run[i:i + 2] for i in range(len(run) - 1)]


def tokenize(text: str) -> List[str]:
    """把文本切成 BM25 用的词项（标识符整体保留，中文分词/二元组）。"""
    out: List[str] = []
    for m in _TOKEN_RE.finditer(_normalize(text)):
        piece = m.group(0).strip(_TRAILING)
        if not piece:
            continue
        if re.match(r"^[\u4e00-\u9fff]+$", piece):
            out.extend(t for t in _cjk_tokens(piece) if t and t not in _STOP)
        else:
            if piece not in _STOP:
                out.append(piece)
    return out


class BM25Index:
    """把语料建成 BM25 索引；不可用时静默降级（`available=False`）。"""

    def __init__(self, texts: Sequence[str]) -> None:
        self._corpus: List[List[str]] = [tokenize(t) for t in texts]
        self._bm25 = None
        if _BM25Okapi is None:
            log.info("rank_bm25 未安装，关键词路关闭（仅向量检索）")
            return
        if not self._corpus:
            return
        try:
            self._bm25 = _BM25Okapi(self._corpus)
        except Exception as e:  # noqa: BLE001 - 空语料/全空 token 等
            log.warning("BM25 索引构建失败，关键词路关闭：%s", e)

    @property
    def available(self) -> bool:
        return self._bm25 is not None

    def scores(self, query: str) -> List[float]:
        """返回与语料等长的分数；不可用或查询为空时全 0。"""
        n = len(self._corpus)
        if self._bm25 is None or n == 0:
            return [0.0] * n
        tokens = tokenize(query)
        if not tokens:
            return [0.0] * n
        try:
            raw = self._bm25.get_scores(tokens)
        except Exception as e:  # noqa: BLE001
            log.warning("BM25 打分失败，本轮关键词路为空：%s", e)
            return [0.0] * n
        return [float(x) for x in raw]


def top_indices(scores: Sequence[float], limit: int, *, positive_only: bool = False) -> List[int]:
    """按分数降序取前 limit 个下标（稳定：同分按原序）。"""
    ranked = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
    if positive_only:
        ranked = [i for i in ranked if scores[i] > 0]
    return ranked[:limit]


def rrf_fuse(rankings: Sequence[Sequence[int]], *, k: int = 60) -> Dict[int, float]:
    """倒数排名融合：score = Σ 1/(k + rank)，rank 从 1 开始。

    只用名次不用原始分——BM25 与余弦的量纲不可比，直接相加需要标定，
    而 RRF 天然免疫尺度问题（官方/社区一致推荐作为默认融合方式）。
    k 取 60 是经验值，作用是控制头部衰减速度，不是准确率旋钮，不需要调。
    """
    fused: Dict[int, float] = {}
    for ranking in rankings:
        for pos, idx in enumerate(ranking, start=1):
            fused[idx] = fused.get(idx, 0.0) + 1.0 / (k + pos)
    return fused
