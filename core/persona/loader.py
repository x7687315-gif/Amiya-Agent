"""YAML 配置加载：identity / speech / behavior / relationship 四份人格配置。

全部位于 config/persona/。人格配置与代码解耦，调语气/价值观只改 YAML。
"""
from __future__ import annotations

import os
from typing import Any, Dict

import yaml


def _load_yaml(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def persona_dir(base_dir: str | None = None) -> str:
    """定位 config/persona 目录。

    base_dir 未传时，以本文件位置反推项目根：
    core/persona/loader.py → core/persona → core → 项目根。
    """
    if base_dir is None:
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
    return os.path.join(base_dir, "config", "persona")


def load_identity(base_dir: str | None = None) -> Dict[str, Any]:
    return _load_yaml(os.path.join(persona_dir(base_dir), "identity.yaml"))


def load_speech(base_dir: str | None = None) -> Dict[str, Any]:
    return _load_yaml(os.path.join(persona_dir(base_dir), "speech.yaml"))


def load_behavior(base_dir: str | None = None) -> Dict[str, Any]:
    return _load_yaml(os.path.join(persona_dir(base_dir), "behavior.yaml"))


def load_relationship(base_dir: str | None = None) -> Dict[str, Any]:
    return _load_yaml(os.path.join(persona_dir(base_dir), "relationship.yaml"))
