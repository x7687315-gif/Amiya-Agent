"""全局语音开关状态（UI 偏好）。

设计约束（M3-C）：
- 纯状态、零 flet 依赖，可被 chat_bubble / header 等任意组件订阅。
- observable：订阅者（每个气泡的 🔊 按钮、Header 的静音键）随状态变化刷新。
- 订阅用强引用持有：订阅者（气泡 🔊 按钮）本就随聊天存活，强引用不会额外泄漏；
  用弱引用持有闭包反而会被立即 GC，导致 toggle 通知不到。
- 默认「开」（unmuted）——静音是用户主动选择，不默认静音。
"""
from __future__ import annotations

from typing import Callable, List, Set


class MuteState:
    """语音开关的可观察状态。"""

    def __init__(self, muted: bool = False) -> None:
        self._muted = muted
        self._subs: Set[Callable[[bool], None]] = set()

    @property
    def muted(self) -> bool:
        return self._muted

    def subscribe(self, cb: Callable[[bool], None]) -> None:
        """注册订阅；注册时立即用当前状态回调一次，使新气泡按钮对齐当前开关。"""
        self._subs.add(cb)
        try:
            cb(self._muted)
        except Exception:  # noqa: BLE001 - 订阅回调异常不影响状态机
            pass

    def toggle(self) -> bool:
        """翻转开关并通知所有订阅者，返回翻转后的 muted 值。"""
        return self.set(not self._muted)

    def set(self, muted: bool) -> bool:
        if self._muted == muted:
            return self._muted
        self._muted = muted
        self._notify()
        return self._muted

    def _notify(self) -> None:
        # 快照为 list，避免回调中增删订阅导致迭代异常
        for cb in list(self._subs):
            try:
                cb(self._muted)
            except Exception:  # noqa: BLE001 - 单个订阅者异常不影响他人
                pass
