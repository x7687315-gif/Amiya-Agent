"""ToolExecutor：执行已授权工具（校验→权限→执行→限长→审计返回）。

契约来源：INTERFACE_CONTRACTS_2.0.md §5 · TOOL_SYSTEM.md §6。
执行管线：
  1 registry.lookup(name)         # 未知工具 → unknown_tool（fail-closed）
  2 幂等检查(tool_call_id)         # 已执行 → 拒绝重复
  3 validator.validate(spec.parameters, args)
  4 permissions.check(spec, args)  # L2 → 审批（fail-closed）
  5 线程池执行 + timeout_s 看门狗   # 超时 → timeout（不强杀线程）
  6 结果限长(result_limit) + 截断标记
  7 返回 ToolResult（异常收敛，绝不向 Runtime 抛裸异常）

线程模型：concurrent.futures 单线程池 + future.result(timeout)；
Python 无法强杀线程，超时后结果丢弃、工具须配合 cancel Event 协作退出。
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable, Optional, Set

from .base import ToolResult, ToolResultErrorCode
from .permissions import ApprovalOutcome, Approver, PermissionManager
from .registry import ToolRegistry
from .validator import SchemaValidator

logger = logging.getLogger(__name__)


class ToolExecutor:
    """工具执行器。持有注册表/校验器/权限器；每个实例维护已执行 tool_call_id 集合。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        validator: SchemaValidator | None = None,
        permissions: PermissionManager | None = None,
    ) -> None:
        self._registry = registry
        self._validator = validator or SchemaValidator()
        self._permissions = permissions or PermissionManager()
        self._executed: Set[str] = set()
        self._executed_lock = threading.RLock()
        # 长驻线程池：不用 `with ThreadPoolExecutor()` —— 其 __exit__ 会 wait 所有线程，
        # 工具睡眠/卡住时 future 超时也无法让调用方及时返回（实测 0.4s 超时变 2s）。
        # 守护线程在后台自然结束，超时对调用方即时生效（Python 无法强杀线程，如实记录）。
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="amiya-tool")

    def execute(
        self,
        tool_call_id: str,
        name: str,
        args: dict,
        *,
        approver: Optional[Approver] = None,
        cancel: Optional[threading.Event] = None,
        on_start: Optional[Callable[[], None]] = None,
    ) -> ToolResult:
        """完整执行管线。任何异常都收敛为 ToolResult，绝不向上抛。

        on_start：**权限判定通过后、真正执行前**的回调（Phase 5A 新增）。
        Runtime 用它发 `tool_started` 事件，让 UI 精确区分「请求中/等审批」
        与「已开始执行」，而不是把两者混成一个状态。
        """
        # 1. 未知工具
        tool = self._registry.lookup(name)
        if tool is None:
            return ToolResult.fail(ToolResultErrorCode.UNKNOWN_TOOL.value,
                                   f"未知工具：{name!r}")
        # 2. 幂等：同 tool_call_id 不重复执行
        with self._executed_lock:
            if tool_call_id in self._executed:
                return ToolResult.fail(ToolResultErrorCode.DUPLICATE_CALL.value,
                                       f"工具调用已执行过：{tool_call_id!r}")
            self._executed.add(tool_call_id)
        spec = tool.spec
        # 3. 参数校验
        bad = self._validator.validate_or_result(spec.parameters, args)
        if bad is not None:
            return bad
        # 4. 权限判定（fail-closed）
        decision = self._permissions.check(spec, args, approver=approver)
        if not decision.allowed:
            return ToolResult.fail(ToolResultErrorCode.PERMISSION_DENIED.value,
                                   f"权限不足：{decision.reason or decision.outcome.value}")
        # 5. 执行（长驻线程池 + 超时；超时对调用方即时返回）
        if on_start is not None:
            try:
                on_start()
            except Exception as exc:  # noqa: BLE001 - 观察者异常不影响执行
                logger.warning("on_start 回调异常（忽略）：%s", exc)
        try:
            if cancel is not None and cancel.is_set():
                return ToolResult.fail(ToolResultErrorCode.EXECUTION_ERROR.value, "已取消")
            future = self._pool.submit(tool.execute, args, cancel=cancel)
            try:
                result = future.result(timeout=spec.timeout_s)
            except FutureTimeout:
                future.cancel()
                return ToolResult.fail(ToolResultErrorCode.TIMEOUT.value,
                                       f"工具执行超时（{spec.timeout_s}s）：{name}")
        except Exception as exc:  # noqa: BLE001 - 工具异常收敛，不崩 Runtime
            logger.warning("工具执行异常：%s：%s", name, exc)
            return ToolResult.fail(ToolResultErrorCode.EXECUTION_ERROR.value,
                                   f"工具执行异常：{type(exc).__name__}: {exc}")
        # 6. 结果限长
        if not isinstance(result, ToolResult):
            return ToolResult.fail(ToolResultErrorCode.EXECUTION_ERROR.value,
                                   "工具未返回 ToolResult")
        if len(result.content) > spec.result_limit:
            truncated = result.content[:spec.result_limit] + "\n[结果已截断]"
            result = ToolResult(result.ok, truncated, result.error_code, result.data)
        return result

    def reset_idempotency(self) -> None:
        """清空幂等集合（仅测试/新 Run 初始化用；生产不应随意调用）。"""
        with self._executed_lock:
            self._executed.clear()
