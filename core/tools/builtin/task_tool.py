"""TaskTool（L1 写入；delete 为 L2 审批）：任务的增删改查。

数据落 assistant_task 表（TaskRepository），绝不写 memory/candidate（三柱隔离）。
due_at 只收 ISO 8601 字符串（自然语言由模型先转换），校验失败回注。
"""
from __future__ import annotations

from datetime import datetime

from ...tasks.repository import TaskRepository
from ..base import PermissionLevel, ToolResult, ToolSpec


class TaskTool:
    """单 Tool 暴露多个 action（首版简化；权限按 action 分级由 policy 决定）。

    注意：delete 的 L2 判定由 ToolSpec.permission 统一标记——
    本工具整体标 L1，delete action 内部再检查 approver（见 IMPL-04 文档说明）。
    首版为保持简单，delete 通过独立 spec 的 TaskDeleteTool 暴露（L2）。
    """

    spec = ToolSpec(
        name="manage_task",
        description="管理个人任务：创建、列出、更新、完成任务。任务不写入长期记忆。",
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "update", "complete"],
                    "description": "create=新建 / list=列出 / update=改字段 / complete=标记完成",
                },
                "task_id": {"type": "string", "description": "任务 ID（update/complete 必填）"},
                "title": {"type": "string", "maxLength": 200, "description": "任务标题（create 必填）"},
                "description": {"type": "string", "maxLength": 2000},
                "due_at": {"type": "string", "description": "ISO 8601 截止时间，如 2026-11-27T18:00"},
                "priority": {"type": "integer", "minimum": 1, "maximum": 10},
                "status": {"type": "string", "enum": ["open", "done", "cancelled"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        permission=PermissionLevel.L1,
        timeout_s=10.0,
    )

    def __init__(self, repo: TaskRepository) -> None:
        self._repo = repo

    def execute(self, args: dict, *, cancel=None) -> ToolResult:
        action = args.get("action")
        try:
            if action == "create":
                title = (args.get("title") or "").strip()
                if not title:
                    return ToolResult.fail("invalid_args", "create 需要 title")
                due = self._validate_due(args.get("due_at"))
                if due is None and args.get("due_at"):
                    return ToolResult.fail("invalid_args", "due_at 须为 ISO 8601 格式")
                tid = self._repo.create(
                    title,
                    description=args.get("description") or "",
                    due_at=due,
                    priority=int(args.get("priority", 5)),
                )
                return ToolResult.success(f"任务已创建：{tid}（{title}）", data={"task_id": tid})
            if action == "list":
                tasks = self._repo.list(status=args.get("status"), limit=int(args.get("limit", 20)))
                if not tasks:
                    return ToolResult.success("暂无任务")
                lines = [f"- [{t.status}] {t.task_id[:8]} {t.title}"
                         + (f" (截止 {t.due_at})" if t.due_at else "") for t in tasks]
                return ToolResult.success("\n".join(lines),
                                          data={"tasks": [t.to_dict() for t in tasks]})
            if action == "update":
                tid = (args.get("task_id") or "").strip()
                if not tid:
                    return ToolResult.fail("invalid_args", "update 需要 task_id")
                fields = {}
                for key in ("title", "description", "priority"):
                    if key in args:
                        fields[key] = args[key]
                if "due_at" in args:
                    due = self._validate_due(args["due_at"])
                    if due is None:
                        return ToolResult.fail("invalid_args", "due_at 须为 ISO 8601 格式")
                    fields["due_at"] = due
                ok = self._repo.update(tid, **fields)
                return (ToolResult.success(f"任务已更新：{tid}") if ok else
                        ToolResult.fail("execution_error", "任务不存在或无字段可更新"))
            if action == "complete":
                tid = (args.get("task_id") or "").strip()
                if not tid:
                    return ToolResult.fail("invalid_args", "complete 需要 task_id")
                ok = self._repo.set_status(tid, "done")
                return (ToolResult.success(f"任务已完成：{tid}") if ok else
                        ToolResult.fail("execution_error", "任务不存在"))
            return ToolResult.fail("invalid_args", f"未知 action：{action!r}")
        except ValueError as exc:
            return ToolResult.fail("invalid_args", str(exc))
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail("execution_error", f"任务操作失败：{type(exc).__name__}: {exc}")

    @staticmethod
    def _validate_due(value) -> str | None | bool:
        if value is None or value == "":
            return None
        try:
            datetime.fromisoformat(value)
            return str(value)
        except (ValueError, TypeError):
            return False


class TaskDeleteTool:
    """删除任务（L2，须审批）：状态改为 cancelled，不物理删数据。"""

    spec = ToolSpec(
        name="delete_task",
        description="取消/删除一个任务（需要用户确认）。任务状态改为 cancelled，历史保留。",
        parameters={
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 ID"}},
            "required": ["task_id"],
            "additionalProperties": False,
        },
        permission=PermissionLevel.L2,
        timeout_s=10.0,
    )

    def __init__(self, repo: TaskRepository) -> None:
        self._repo = repo

    def execute(self, args: dict, *, cancel=None) -> ToolResult:
        tid = (args.get("task_id") or "").strip()
        if not tid:
            return ToolResult.fail("invalid_args", "delete_task 需要 task_id")
        ok = self._repo.set_status(tid, "cancelled")
        return (ToolResult.success(f"任务已取消：{tid}") if ok else
                ToolResult.fail("execution_error", "任务不存在"))
