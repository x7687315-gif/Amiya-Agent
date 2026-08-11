"""Knowledge RAG 单测：语料切块、检索相关度、三柱定界、Agent 接线。

覆盖：KnowledgeCorpus 按标题切块、KnowledgeRetriever 相关度排序、
render_block 定界、PromptBuilder 三柱分离且 score= 不泄漏、
Agent 每轮用原话检索知识并独立注入（与记忆不混）。
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

from core.knowledge import KnowledgeCorpus, KnowledgeManager, build_knowledge_manager
from core.knowledge.loader import KnowledgeChunk
from core.knowledge.retriever import KnowledgeRetriever
from core.memory.embedder import HashingEmbedder
from core.persona import load_persona
from core.prompt_builder import PromptBuilder

# 允许 `python -m pytest` 直接运行
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _write(dir_path: Path, filename: str, content: str) -> None:
    (dir_path / filename).write_text(content, encoding="utf-8")


def _tmp_knowledge() -> Path:
    d = Path(tempfile.mkdtemp()) / "knowledge"
    d.mkdir(parents=True)
    _write(
        d,
        "rel.md",
        "# 助手与用户\n## 关系\n助手对用户有近乎无条件的信任，视其为最可靠的伙伴。\n",
    )
    _write(
        d,
        "world.md",
        "# 与矿石病\n## 定义\n带来法术，也带来无法根治的矿石病，感染者常被世俗排斥。\n",
    )
    return d


# ----- 切块 -----
def test_corpus_splits_by_heading():
    chunks = KnowledgeCorpus(_tmp_knowledge()).chunks
    # 两个 md 各含 # + ## 两个标题 → 共 4 个 chunk
    assert len(chunks) == 4
    assert all(isinstance(c, KnowledgeChunk) for c in chunks)
    rel = next(c for c in chunks if c.heading == "关系")
    assert "用户" in rel.text and "助手" in rel.text
    # id 稳定可复现（rel.md 内第 2 个 chunk → ::1）
    assert rel.id == "rel.md::1"


def test_corpus_no_heading_uses_filename():
    d = Path(tempfile.mkdtemp()) / "knowledge"
    d.mkdir(parents=True)
    _write(d, "plain.md", "助手是本地的公开领导者。\n她外表是兽耳少女。")
    chunks = KnowledgeCorpus(d).chunks
    assert len(chunks) == 1
    assert chunks[0].heading == "plain"  # 文件名 stem
    assert "本地" in chunks[0].text


def test_corpus_missing_dir_is_empty():
    chunks = KnowledgeCorpus(Path(tempfile.mkdtemp()) / "nope").chunks
    assert chunks == []


# ----- 检索 -----
def test_retriever_ranks_relevant_chunk_first():
    emb = HashingEmbedder(dim=256)
    corpus = KnowledgeCorpus(_tmp_knowledge())
    retr = KnowledgeRetriever(corpus, emb, top_k=4)
    hits = retr.retrieve("助手和用户是什么关系")
    assert hits, "应检索到命中"
    assert "用户" in hits[0].text, "最相关应是关系片段"
    assert hits[0].channels.get("keyword", 0) > 0
    assert "score" not in hits[0].channels  # channels 里只有 vector/keyword
    # 片段不应排在最前
    assert "" not in hits[0].text


def test_retriever_empty_corpus_returns_nothing():
    emb = HashingEmbedder(dim=256)
    retr = KnowledgeRetriever(KnowledgeCorpus(Path(tempfile.mkdtemp()) / "empty"), emb)
    assert retr.retrieve("任何问题") == []


def test_render_block_delimited_and_no_scores():
    emb = HashingEmbedder(dim=256)
    retr = KnowledgeRetriever(KnowledgeCorpus(_tmp_knowledge()), emb, top_k=4)
    hits = retr.retrieve("助手和用户是什么关系")
    block = retr.render_block(hits)
    assert block.startswith("【角色知识】")
    assert "助手" in block
    assert "score=" not in block  # 分数绝不进提示词（防注入）


# ----- 管理器 / 工厂 -----
def test_manager_retrieve_and_render():
    km = KnowledgeManager(_tmp_knowledge(), HashingEmbedder(dim=256), top_k=4)
    assert not km.is_empty
    hits = km.retrieve("助手和用户是什么关系")
    assert hits and km.render_block(hits).startswith("【角色知识】")


def test_build_knowledge_manager_falls_back_to_none_on_missing_dir():
    km = build_knowledge_manager(
        Path(tempfile.mkdtemp()) / "gone",
        backend="hashing",
    )
    # 目录不存在 → 语料为空，但管理器仍可用（检索返回空）
    assert km is not None
    assert km.is_empty
    assert km.retrieve("x") == []


# ----- 三柱定界（PromptBuilder）-----
def test_build_system_three_pillars_separated():
    pb = PromptBuilder(load_persona())
    sys_p = pb.build_system(
        memory_block="- (偏好) 用户喜欢先规划架构再写代码",
        knowledge_block="【角色知识】\n- (rel.md › 关系) 助手信任用户",
        emotion_block="【当前情绪】平静而专注",
    )
    # 三柱各自独立、顺序正确：身份 → 知识 → 记忆 → 情绪
    i_knowledge = sys_p.index("【角色知识】")
    i_memory = sys_p.index("【相关用户记忆】")
    i_emotion = sys_p.index("【当前情绪】")
    assert i_knowledge < i_memory < i_emotion
    # 各自内容互不串台
    assert "助手信任用户" in sys_p[i_knowledge:i_memory]
    assert "用户喜欢先规划架构再写代码" in sys_p[i_memory:i_emotion]
    assert "平静而专注" in sys_p[i_emotion:]
    # 分数不泄漏
    assert "score=" not in sys_p


def test_build_system_omits_empty_pillars():
    pb = PromptBuilder(load_persona())
    sys_p = pb.build_system()  # 全空
    assert "【角色知识】" not in sys_p
    assert "【相关用户记忆】" not in sys_p
    assert "【当前情绪】" not in sys_p
    # 身份锚定仍在
    assert "助手" in sys_p


# ----- Agent 接线（知识独立注入，与记忆不混）-----
class _FakeKnowledge:
    def __init__(self, block: str) -> None:
        self._block = block
        self.queries: list[str] = []

    def retrieve(self, query, top_k=None):
        self.queries.append(query)
        from core.knowledge.retriever import KnowledgeHit

        return [
            KnowledgeHit(
                id="k1",
                source="rel.md",
                heading="关系",
                text="助手信任用户",
                score=0.9,
                channels={"vector": 0.9, "keyword": 0.0},
            )
        ]

    def render_block(self, hits):
        return self._block


class _CaptureLLM:
    def __init__(self) -> None:
        self.system = None

    def stream_chat(self, system_prompt, window):
        self.system = system_prompt
        return iter([])


def test_agent_retrieves_knowledge_each_turn():
    fake = _FakeKnowledge("【角色知识】\n- (rel.md › 关系) 助手信任用户")
    llm = _CaptureLLM()
    from core.agent import Agent

    agent = Agent(
        persona=load_persona(),
        llm=llm,
        knowledge=fake,
        knowledge_top_k=3,
    )
    list(agent.reply("用户问：你和我的关系是什么？"))
    # Agent 用原话去检索知识
    assert fake.queries == ["用户问：你和我的关系是什么？"]
    # 知识块被独立注入 system 提示词
    assert llm.system is not None
    assert "【角色知识】" in llm.system
    assert "助手信任用户" in llm.system
    # 与记忆块不混（这里没传记忆，确认没有凭空出现记忆块）
    assert "【相关用户记忆】" not in llm.system
