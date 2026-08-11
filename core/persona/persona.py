"""人格加载：从 config/persona 读取身份与语言风格 YAML。

人格与代码解耦——调语气/认知视角只改 YAML，不碰 Python。
（由 core/persona.py 迁入本包；worldview 字段已改名为 perspective。）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .loader import load_identity, load_speech


@dataclass
class Persona:
    name: str
    title: str
    identity: Dict[str, Any]
    speech: Dict[str, Any]

    @property
    def address(self) -> str:
        return self.speech.get("address", "用户")

    @property
    def examples(self) -> List[Dict[str, str]]:
        return self.speech.get("examples", [])


def load_persona(base_dir: str | None = None) -> Persona:
    identity = load_identity(base_dir)
    speech = load_speech(base_dir)
    return Persona(
        name=identity.get("name", "助手"),
        title=identity.get("title", ""),
        identity=identity,
        speech=speech,
    )
