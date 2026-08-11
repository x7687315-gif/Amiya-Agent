"""Knowledge 语料加载：把 knowledge/*.md 切成可检索的 chunk。

切块策略：以 Markdown 标题（# ~ ######）为边界。一个标题到下一个标题之间的正文
成为一个 chunk；文件开头没有标题的段落归到以"文件名"为标题的 chunk。这样每条知识
都带 (源文件, 标题) 元信息，检索命中时能在提示词里标明出处，也方便人工校订。

本文件**只做纯文本解析**，不触发任何嵌入/网络/存储 IO，保持可单测、可离线。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class KnowledgeChunk:
    """一条知识片段。id 稳定可复现（源文件::序号），便于缓存与去重。"""

    id: str
    source: str  # 文件名，如 assistant_persona.md
    heading: str  # 该片段所属标题（无标题时用文件名）
    text: str  # 标题 + 正文（含标题，便于检索时带上下文）


class KnowledgeCorpus:
    """加载并切分 knowledge 目录下的全部 Markdown。"""

    def __init__(self, directory: "str | Path") -> None:
        self.directory = Path(directory)
        self.chunks: list[KnowledgeChunk] = self._load()

    def _load(self) -> list[KnowledgeChunk]:
        chunks: list[KnowledgeChunk] = []
        if not self.directory.is_dir():
            return chunks
        for path in sorted(self.directory.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            chunks.extend(self._split(text, path.name))
        return chunks

    @staticmethod
    def _split(text: str, source: str) -> list[KnowledgeChunk]:
        lines = text.splitlines()
        out: list[KnowledgeChunk] = []
        cur_heading: str | None = None
        cur_body: list[str] = []
        idx = 0

        def flush() -> None:
            nonlocal cur_heading, cur_body, idx
            body = "\n".join(cur_body).strip()
            if body or cur_heading:
                heading = cur_heading or Path(source).stem
                full = (f"{cur_heading}\n" if cur_heading else "") + body
                out.append(
                    KnowledgeChunk(
                        id=f"{source}::{idx}",
                        source=source,
                        heading=heading,
                        text=full.strip(),
                    )
                )
                idx += 1
            cur_body = []

        for line in lines:
            m = _HEADING.match(line)
            if m:
                flush()
                cur_heading = m.group(2).strip()
            else:
                cur_body.append(line)
        flush()
        return out
