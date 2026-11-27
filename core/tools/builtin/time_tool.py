"""TimeTool（L0）：精确查询当前日期/时间/星期/ISO 周数。

与 prompt 里的【当前时间】块互补：prompt 给粗粒度背景，工具给精确查询
（模型可主动查时区换算）。先用 zoneinfo 标准库，零依赖。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..base import PermissionLevel, ToolResult, ToolSpec


class TimeTool:
    spec = ToolSpec(
        name="get_time",
        description="获取指定时区的当前日期、时间、星期和 ISO 周数。不传时区则用本机时区。",
        parameters={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": "IANA 时区名，如 Asia/Shanghai、UTC；省略用本机时区",
                    "maxLength": 64,
                }
            },
            "additionalProperties": False,
        },
        permission=PermissionLevel.L0,
        timeout_s=5.0,
    )

    def execute(self, args: dict, *, cancel=None) -> ToolResult:
        tz_name = (args.get("timezone") or "").strip()
        try:
            tz = ZoneInfo(tz_name) if tz_name else None
        except (ZoneInfoNotFoundError, ValueError):
            return ToolResult.fail("invalid_args", f"未知时区：{tz_name!r}")
        try:
            now = datetime.now(tz) if tz else datetime.now().astimezone()
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail("execution_error", f"取时间失败：{exc}")
        wd = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][now.weekday()]
        iso = now.isocalendar()
        text = (f"{now:%Y-%m-%d %H:%M:%S} {wd} "
                f"(ISO {iso.year}-W{iso.week:02d}-{iso.weekday}, {now.tzname() or 'local'})")
        return ToolResult.success(text, data={"iso": now.isoformat()})
