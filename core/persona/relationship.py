"""关系状态系统：trust / stage 等系统状态，独立于 Memory（存 persona_state）。

设计要点：
- 状态物理上位于 store 的 `persona_state` 表，与 `memory`（用户事实/经历）分表。
- RelationshipManager 通过 MemoryManager（sanctioned 入口）读写，不直接持有 store。
- stage 仅作为「背景事实」注入提示词（【与用户的关系】），**绝不控制语气**；
  语气由 speech.yaml 决定（延续「stage 不控语气」的裁定）。
- memory=None（未启用记忆/Phase 1）时，block() 返回 None，不注入任何关系块。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .loader import load_relationship


class RelationshipManager:
    def __init__(self, memory=None, base_dir: str | None = None) -> None:
        self._memory = memory
        self._cfg = load_relationship(base_dir)
        self._stages: Dict[str, Dict[str, Any]] = self._cfg.get("stages", {})
        self._default_stage = self._cfg.get("default_stage", "信任")

    # ----- 读取（经 MemoryManager） -----
    def _state(self) -> Dict[str, object]:
        if self._memory is None:
            return {}
        return self._memory.state()

    @property
    def trust(self) -> int:
        return int(self._state().get("trust", 70))

    @property
    def companionship_seconds(self) -> int:
        return int(self._state().get("companionship_seconds", 0))

    @property
    def stage(self) -> str:
        """根据当前 trust 选出命中的最高阶段（stages 按阈值升序判定）。"""
        trust = self.trust
        chosen = self._default_stage
        for name, spec in sorted(
            self._stages.items(), key=lambda kv: int(kv[1].get("threshold", 0))
        ):
            if trust >= int(spec.get("threshold", 0)):
                chosen = name
        return chosen

    # ----- 写入（系统状态管理；调用留待后续阶段） -----
    def set_trust(self, value: int) -> None:
        if self._memory is not None:
            self._memory.save_state(trust=max(0, min(100, int(value))))

    def bump_companionship(self, seconds: int) -> int:
        if self._memory is None:
            return 0
        return self._memory.bump_companionship(seconds)

    # ----- 提示词块 -----
    def block(self) -> Optional[str]:
        """动态关系块【与用户的关系】；memory=None 时返回 None。

        只陈述关系阶段与背景事实，明确指示「自然体现、不改语气」，
        防止 stage 被模型当成语气开关。
        """
        if self._memory is None:
            return None
        stage = self.stage
        spec = self._stages.get(stage, {})
        background: List[str] = spec.get("background", [])
        lines = [f"当前与用户的关系阶段：{stage}。"]
        if background:
            lines.append("关系背景：")
            lines += [f"- {b}" for b in background]
        lines.append(
            "这是你和用户之间关系的事实背景，自然体现即可；"
            "不要刻意强调阶段，也不要因此改变说话方式或语气体贴度。"
        )
        return "【与用户的关系】\n" + "\n".join(lines)
