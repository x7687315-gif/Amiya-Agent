"""ToolRegistry：注册 / 发现 / 白名单导出。

借鉴 harness（docs/41）：register 返回 disposer（为 Phase 6 MCP 动态卸载铺路）；
白名单导出只给模型 name/description/parameters（execute/permission/timeout 不外泄）。
"""
from __future__ import annotations

import threading
from typing import Callable, Dict, List, Optional

from .base import Tool, ToolSpec


class ToolRegistry:
    """线程安全的工具注册表。首版内置工具启动时静态注册。"""

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}
        self._lock = threading.RLock()

    def register(self, tool: Tool) -> Callable[[], None]:
        """注册工具，返回 disposer（调用后从注册表移除）。重复 name 拒绝。"""
        spec: ToolSpec = tool.spec
        with self._lock:
            if spec.name in self._tools:
                raise ValueError(f"工具已注册: {spec.name!r}")
            self._tools[spec.name] = tool

        def dispose() -> None:
            with self._lock:
                self._tools.pop(spec.name, None)

        return dispose

    def lookup(self, name: str) -> Optional[Tool]:
        with self._lock:
            return self._tools.get(name)

    def schemas(self) -> List[dict]:
        """白名单导出：只含 name/description/parameters（模型可见的安全面）。"""
        with self._lock:
            return [t.spec.white_listed_schema() for t in sorted(
                self._tools.values(), key=lambda x: x.spec.name)]

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._tools)

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)
