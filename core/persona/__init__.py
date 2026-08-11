"""Persona 子系统包。

兼容旧 import：
    from core.persona import Persona, load_persona
    from core.persona import PersonaManager
"""
from .persona import Persona, load_persona
from .persona_manager import PersonaManager

__all__ = ["Persona", "load_persona", "PersonaManager"]
