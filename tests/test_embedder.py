"""Embedder 单测：哈希回退的确定性、维度、分词、cosine。

重点防御「用内置 hash() 算向量」的静默 bug——本测试锁定 blake2b 的行为：
同一输入在任何进程、任何启动时都得出完全相同的向量。
"""
import math

from core.memory.embedder import (
    DEFAULT_MODEL,
    FALLBACK_MODEL_NAME,
    HashingEmbedder,
    LocalEmbedder,
    cosine,
    get_embedder,
    local_backend_available,
    tokenize,
)


def test_tokenize_mixes_cjk_and_ascii():
    toks = tokenize("用户喜欢Cat")
    # 中文单字 + 相邻二字组，英文整词
    assert "用" in toks and "户" in toks
    assert "用户" in toks  # 二字组比单字更有区分度
    assert "cat" in toks


def test_hashing_is_deterministic_across_instances():
    a = HashingEmbedder(dim=128)
    b = HashingEmbedder(dim=128)
    v1 = a.encode(["用户养了一只猫"])[0]
    v2 = b.encode(["用户养了一只猫"])[0]
    assert v1 == v2  # 不依赖进程内 hash 盐（blake2b）


def test_hashing_dim_and_normalized():
    emb = HashingEmbedder(dim=64)
    vec = emb.encode(["任意文字"])[0]
    assert len(vec) == 64
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-6  # 已 L2 归一化


def test_hashing_name_records_dim():
    emb = HashingEmbedder(dim=200)
    assert emb.name == f"{FALLBACK_MODEL_NAME}-200"
    assert emb.dim == 200


def test_different_texts_produce_different_vectors():
    emb = HashingEmbedder(dim=256)
    v_a = emb.encode(["我喜欢猫"])[0]
    v_b = emb.encode(["我喜欢狗"])[0]
    assert v_a != v_b
    assert cosine(v_a, v_b) >= 0.0  # 哈希向量非负分量，余弦必非负


def test_cosine_orthogonal_identical_and_guards():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert abs(cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
    assert cosine([], []) == 0.0
    assert cosine([1.0], [1.0, 2.0]) == 0.0  # 维度不一致视为不可比


def test_get_embedder_hashing_backend():
    emb = get_embedder(backend="hashing")
    assert isinstance(emb, HashingEmbedder)


def test_local_backend_available_is_bool():
    assert isinstance(local_backend_available(), bool)


def test_local_embedder_name_is_model_without_loading():
    # 不触发模型加载（懒加载），仅验证 name 属性走模型名
    emb = LocalEmbedder(model_name=DEFAULT_MODEL)
    assert emb.name == DEFAULT_MODEL


# ---------------------------------------------------------------------------
# 离线优先（2026-08-17 启动卡死修复）：缓存命中跳过 HF 更新探测
# ---------------------------------------------------------------------------


def test_hf_model_cache_dir_hit_and_miss(tmp_path, monkeypatch):
    from core.memory.embedder import hf_model_cache_dir

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    assert hf_model_cache_dir("BAAI/bge-small-zh-v1.5") is None  # 未缓存

    model_dir = tmp_path / "hub" / "models--BAAI--bge-small-zh-v1.5"
    model_dir.mkdir(parents=True)
    got = hf_model_cache_dir("BAAI/bge-small-zh-v1.5")
    assert got == model_dir


def test_hf_model_cache_dir_respects_hub_cache_env(tmp_path, monkeypatch):
    from core.memory.embedder import hf_model_cache_dir

    other = tmp_path / "custom"
    (other / "models--X--Y").mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_CACHE", str(other))
    assert hf_model_cache_dir("X/Y") == other / "models--X--Y"


def _fake_sentence_transformers(monkeypatch, seen):
    """注入假 sentence_transformers：构造时记录当时的 HF_HUB_OFFLINE 值。"""
    import sys
    import types

    class _FakeST:
        def __init__(self, name, device="cpu"):
            import os

            seen.append(os.environ.get("HF_HUB_OFFLINE"))

        def get_sentence_embedding_dimension(self):
            return 8

    mod = types.ModuleType("sentence_transformers")
    mod.SentenceTransformer = _FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", mod)


def test_local_embedder_offline_when_cached(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.delenv("HF_HUB_CACHE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    (tmp_path / "hub" / "models--BAAI--bge-small-zh-v1.5").mkdir(parents=True)

    seen = []
    _fake_sentence_transformers(monkeypatch, seen)
    e = LocalEmbedder("BAAI/bge-small-zh-v1.5")
    assert e.dim == 8  # 触发懒加载
    assert seen == ["1"]  # 构造时处于离线模式（跳过 HF 探测）
    assert "HF_HUB_OFFLINE" not in os.environ  # 用完恢复，不污染全局
    assert "TRANSFORMERS_OFFLINE" not in os.environ


def test_local_embedder_no_offline_env_when_not_cached(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("HF_HOME", str(tmp_path))  # 缓存目录为空
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)

    seen = []
    _fake_sentence_transformers(monkeypatch, seen)
    e = LocalEmbedder("BAAI/bge-small-zh-v1.5")
    assert e.dim == 8
    assert seen == [None]  # 未缓存不强行离线（保留联网下载能力）


def test_local_embedder_respects_user_offline_setting(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("HF_HOME", str(tmp_path))
    (tmp_path / "hub" / "models--BAAI--bge-small-zh-v1.5").mkdir(parents=True)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")  # 用户显式设置：不覆盖、不回收

    seen = []
    _fake_sentence_transformers(monkeypatch, seen)
    e = LocalEmbedder("BAAI/bge-small-zh-v1.5")
    assert e.dim == 8
    assert seen == ["0"]
    assert os.environ.get("HF_HUB_OFFLINE") == "0"
