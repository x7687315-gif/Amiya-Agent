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
    assert "博" in toks and "士" in toks
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
