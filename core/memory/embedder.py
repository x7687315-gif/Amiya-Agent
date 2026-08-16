"""嵌入层：Embedder 协议 + 本地模型实现 + 纯 stdlib 回退。

设计目标（低资源本地开发环境）：
- **不占显存**：默认 `BAAI/bge-small-zh-v1.5`（33M 参数），device 默认 cpu。
  4GB 显存的笔记本 GPU 留给别的用途，这个量级的模型 CPU 推理毫秒级足够。
- **不做硬依赖**：sentence-transformers 未安装 / 权重下载失败时，自动退到
  `HashingEmbedder`（纯 stdlib）。功能不缺失，只是语义精度降级。
- **可替换**：模型名与后端都走配置，换 bge-m3 或别的模型只改 .env + reindex()。

为什么默认中文模型：本项目是中文长期陪伴 Agent，记忆内容形如
「我准备去大学」「我昨天很焦虑」。all-MiniLM-L6-v2 是英文模型，
在这些句子上的语义检索质量会明显下降。
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import math
import os
import re
from array import array
from pathlib import Path
from typing import List, Optional, Protocol, Sequence, runtime_checkable

log = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
FALLBACK_MODEL_NAME = "hashing-zh-v1"
FALLBACK_DIM = 256


def hf_model_cache_dir(model_name: str) -> Optional[Path]:
    """模型在本地 HuggingFace 缓存中的目录；未缓存返回 None。

    只做目录存在性探测（零网络、零导入），尊重 HF_HUB_CACHE / HF_HOME
    环境变量，默认 ~/.cache/huggingface/hub。目录布局形如
    ``models--BAAI--bge-small-zh-v1.5``（组织/模型名中的 / 换成 --）。
    """
    hub_root = os.environ.get("HF_HUB_CACHE")
    if not hub_root:
        home = os.environ.get("HF_HOME") or os.path.join(
            os.path.expanduser("~"), ".cache", "huggingface"
        )
        hub_root = os.path.join(home, "hub")
    candidate = Path(hub_root) / ("models--" + model_name.replace("/", "--"))
    return candidate if candidate.is_dir() else None

_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_CJK_RUN = re.compile(f"[{_CJK}]+")
_ASCII_RUN = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    """检索层只依赖这个协议，不关心背后是本地模型还是哈希回退。"""

    @property
    def name(self) -> str:
        """写进 memory.embedding_model 的标识，用于判断向量是否可比。"""
        ...

    @property
    def dim(self) -> int: ...

    def encode(self, texts: Sequence[str]) -> List[List[float]]: ...


def tokenize(text: str) -> List[str]:
    """中英混排分词，供哈希嵌入与关键词通道共用。

    中文没有空格，靠单字切分区分度太低（「大学」和「大」「学」会混），
    因此中文取「单字 + 相邻二字」，并给二字组更高权重。
    英文数字取整词，长词再补 3-gram 以容忍拼写变化。
    """
    text = (text or "").strip().lower()
    if not text:
        return []
    toks: List[str] = []
    for m in _CJK_RUN.finditer(text):
        run = m.group()
        toks.extend(run)  # 单字
        toks.extend(run[i : i + 2] for i in range(len(run) - 1))  # 二字组
    for m in _ASCII_RUN.finditer(text):
        w = m.group()
        toks.append(w)
        if len(w) > 4:
            toks.extend(w[i : i + 3] for i in range(len(w) - 2))
    return toks


def _stable_bucket(token: str, dim: int) -> tuple:
    """稳定哈希 → (下标, 符号)。

    **必须用 blake2b 而不是内置 hash()**：CPython 的 str 哈希按进程随机加盐
    （PYTHONHASHSEED），用它算出的向量重启后就对不上了——而且不会报错，
    只会让检索悄悄退化成随机结果。这类 bug 极难从现象反查。
    """
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    idx = int.from_bytes(h[:4], "big") % dim
    sign = 1.0 if h[4] & 1 else -1.0
    return idx, sign


def _l2_normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm < 1e-12:
        return vec
    return [v / norm for v in vec]


class HashingEmbedder:
    """纯 stdlib 的字符 n-gram 哈希嵌入（hashing trick）。

    定位是**保底**而非最优：它捕捉的是字面重合而非语义，
    「喜欢猫」和「养了只猫咪」相似度会偏低。但它离线可用、零依赖、
    毫秒级，足以让整条检索链路在没装 torch 的机器上完整跑通。
    """

    def __init__(self, dim: int = FALLBACK_DIM) -> None:
        self._dim = dim

    @property
    def name(self) -> str:
        return f"{FALLBACK_MODEL_NAME}-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def encode(self, texts: Sequence[str]) -> List[List[float]]:
        out: List[List[float]] = []
        for text in texts:
            vec = [0.0] * self._dim
            for tok in tokenize(text):
                # 二字组比单字更有区分度，给更高权重
                weight = 1.0 if len(tok) > 1 else 0.4
                idx, sign = _stable_bucket(tok, self._dim)
                vec[idx] += sign * weight
            out.append(_l2_normalize(vec))
        return out


class LocalEmbedder:
    """sentence-transformers 本地模型，懒加载。

    懒加载的意义：应用启动不该为了嵌入模型卡住几秒。首次真正需要
    向量时才加载，之后常驻内存（bge-small 约 130MB，16GB 内存无压力）。
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        device: str = "cpu",
        query_instruction: str = "",
    ) -> None:
        self._model_name = model_name
        self._device = device
        # bge v1.5 官方说明：检索场景可不加指令前缀。保留此开关是为了
        # 换用 bge-large / bge-m3 等需要指令的模型时无需改代码。
        self._query_instruction = query_instruction
        self._model = None
        self._dim: Optional[int] = None

    @property
    def name(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._ensure_model()
        return int(self._dim or 0)

    def _ensure_model(self):
        if self._model is not None:
            return self._model

        # 离线优先（2026-08-17 启动卡死修复）：模型已在本地缓存时，跳过
        # huggingface.co 的更新探测。网络不可达环境下每次 HEAD 超时（约 21s）
        # × 5 次重试会把首次加载卡住数分钟——而权重明明就在缓存里。
        #
        # ⚠ 顺序关键：huggingface_hub 的 constants 在 **import 时快照**
        # HF_HUB_OFFLINE（实测 1.26.0），因此环境变量必须设在
        # `from sentence_transformers import ...` 之前，晚设无效。
        # 已验证的教训：先 import 再设变量，日志会打出"离线加载"但探测照发。
        offline_cache_hit = hf_model_cache_dir(self._model_name) is not None
        _restored: List[str] = []
        if offline_cache_hit:
            for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
                if key not in os.environ:
                    os.environ[key] = "1"
                    _restored.append(key)

        try:
            from sentence_transformers import SentenceTransformer  # 延迟导入

            log.info("正在加载嵌入模型 %s (device=%s)…", self._model_name, self._device)
            if _restored:
                log.info("嵌入模型已在本地缓存，离线加载（跳过 HF 更新探测）")
            if offline_cache_hit:
                # 双保险：显式告知只用本地文件（新版 ST 支持；旧版不支持则回退）
                try:
                    self._model = SentenceTransformer(
                        self._model_name, device=self._device, local_files_only=True
                    )
                except TypeError:  # pragma: no cover - 旧版签名无此参数
                    self._model = SentenceTransformer(self._model_name, device=self._device)
            else:
                self._model = SentenceTransformer(self._model_name, device=self._device)
        finally:
            # 构造完成即恢复环境变量，不污染进程全局配置。
            # 注意：hub 的 constants 若已快照为 offline，对进程后续仍生效——
            # 本进程只加载这一个嵌入模型，无副作用。
            for key in _restored:
                os.environ.pop(key, None)
        self._dim = int(self._model.get_sentence_embedding_dimension())
        return self._model

    def encode(self, texts: Sequence[str]) -> List[List[float]]:
        model = self._ensure_model()
        payload = [f"{self._query_instruction}{t}" for t in texts]
        vecs = model.encode(
            payload,
            normalize_embeddings=True,  # 归一化后余弦相似度 == 点积
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [list(map(float, v)) for v in vecs]


def local_backend_available() -> bool:
    """只检查包是否可导入，不触发模型加载（那会下载上百 MB 权重）。"""
    return importlib.util.find_spec("sentence_transformers") is not None


def get_embedder(
    backend: str = "auto",
    model_name: str = DEFAULT_MODEL,
    device: str = "cpu",
    dim: int = FALLBACK_DIM,
) -> Embedder:
    """按后端名构造嵌入器。

    - ``auto``（默认）：能导入 sentence-transformers 就用本地模型，否则回退。
    - ``local``：强制本地模型，缺依赖直接抛错（用于确认环境真的装好了）。
    - ``hashing`` / ``fallback``：强制纯 stdlib，离线与 CI 用。

    auto 只做「包能否导入」的廉价探测，不预加载模型：预加载会让
    启动多等数秒，且在没有权重缓存时触发下载。真正的加载失败由
    调用方（MemoryManager）捕获并降级——向量缺失时检索仍有
    关键词 / 时间 / 权重三条通道可用。
    """
    backend = (backend or "auto").strip().lower()
    if backend in ("hashing", "fallback"):
        return HashingEmbedder(dim=dim)
    if backend == "local":
        if not local_backend_available():
            raise RuntimeError(
                "EMBEDDING_BACKEND=local 但未安装 sentence-transformers。"
                "请执行 pip install sentence-transformers，或改用 auto / hashing。"
            )
        return LocalEmbedder(model_name=model_name, device=device)
    if backend != "auto":
        log.warning("未知 EMBEDDING_BACKEND=%r，按 auto 处理", backend)
    if local_backend_available():
        return LocalEmbedder(model_name=model_name, device=device)
    log.info("未检测到 sentence-transformers，嵌入回退为纯 stdlib 哈希实现")
    return HashingEmbedder(dim=dim)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度。两侧都已 L2 归一化时等价于点积，但这里不做该假设。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / math.sqrt(na * nb)


def pack(vec: Sequence[float]) -> bytes:
    """float32 打包，与 store 的 BLOB 存储格式保持一致。"""
    return array("f", vec).tobytes()
