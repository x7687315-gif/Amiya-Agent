"""Skills（2.x Phase 6）：`skills/<name>/SKILL.md` 的加载、目录与按需读取。

与 Tool 的关系：技能不是工具，也不产生新的执行路径——它只是**文字指引**；
技能里声明的工具仍是普通本地工具，照旧过 Registry + PermissionPolicy。

契约：`MIGRATION_PLAN_2.0.md` §3 Phase 6 / `ARCHITECTURE_2.0.md` §6。
"""
from .loader import Skill, load_skills, parse_skill_md
from .registry import SkillRegistry
from .tool import SkillTool

__all__ = ["Skill", "load_skills", "parse_skill_md", "SkillRegistry", "SkillTool"]
