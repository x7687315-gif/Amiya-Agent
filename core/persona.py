"""人格加载：从 config/persona 读取身份与语言风格 YAML。

人格与代码解耦——调语气/世界观只改 YAML，不碰 Python。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List

import yaml


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


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_persona(base_dir: str | None = None) -> Persona:
    if base_dir is None:
        # core/ -> 项目根
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    persona_dir = os.path.join(base_dir, "config", "persona")
    identity = _load_yaml(os.path.join(persona_dir, "identity.yaml"))
    speech = _load_yaml(os.path.join(persona_dir, "speech.yaml"))
    return Persona(
        name=identity.get("name", "助手"),
        title=identity.get("title", ""),
        identity=identity,
        speech=speech,
    )
