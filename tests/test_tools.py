"""Tool System（Phase 2A）离线测试：五类拦截 + 白名单 + 幂等 + L3 禁用。

所有测试零网络、零真实工具副作用；cancel/审批器用内存替身。
"""
import threading
import time

import pytest

from core.tools import (
    ApprovalOutcome,
    PermissionLevel,
    PermissionManager,
    SchemaValidator,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from core.tools.base import ToolResultErrorCode
from core.tools.permissions import Approver


class _EchoTool:
    def __init__(self, *, permission=PermissionLevel.L0, delay=0.0,
                 raise_exc=False, spec_name="echo"):
        self.spec = ToolSpec(
            name=spec_name,
            description="回显输入（测试工具）",
            parameters={
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 20}},
                "required": ["text"],
                "additionalProperties": False,
            },
            permission=permission,
            timeout_s=0.4,
        )
        self._delay = delay
        self._raise = raise_exc

    def execute(self, args, *, cancel=None):
        if self._delay:
            time.sleep(self._delay)
        if self._raise:
            raise RuntimeError("工具内部炸了")
        return ToolResult.success(args["text"])


class _Approver:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def ask(self, *, tool_name, reason, args):
        self.calls.append(tool_name)
        return self.outcome


def _registry_with(tool):
    r = ToolRegistry()
    r.register(tool)
    return r


# ---------- base / registry ----------

def test_spec_rejects_bad_name():
    with pytest.raises(ValueError):
        ToolSpec("Bad-Name", "x", {"type": "object"}, PermissionLevel.L0)


def test_spec_rejects_l3():
    with pytest.raises(ValueError, match="L3"):
        ToolSpec("danger", "x", {"type": "object"}, PermissionLevel.L3)


def test_registry_whitelist_schema():
    r = _registry_with(_EchoTool())
    schemas = r.schemas()
    assert len(schemas) == 1
    fn = schemas[0]["function"]
    assert set(fn) == {"name", "description", "parameters"}
    assert "execute" not in fn and "permission" not in fn and "timeout_s" not in fn


def test_registry_duplicate_rejected_and_disposer():
    r = ToolRegistry()
    tool = _EchoTool()
    dispose = r.register(tool)
    with pytest.raises(ValueError):
        r.register(_EchoTool())
    dispose()
    assert len(r) == 0


# ---------- validator ----------

def test_validator_required_and_extra():
    v = SchemaValidator()
    spec = _EchoTool().spec
    assert v.validate(spec.parameters, {"text": "hi"})[0] is True
    ok, msg = v.validate(spec.parameters, {"wrong": "hi"})
    assert not ok and "缺少" in msg
    ok, msg = v.validate(spec.parameters, {"text": "hi", "extra": 1})
    assert not ok and "额外字段" in msg
    ok, msg = v.validate(spec.parameters, {"text": "x" * 21})
    assert not ok and "长度" in msg


# ---------- executor 五类拦截 ----------

def test_unknown_tool():
    ex = ToolExecutor(ToolRegistry())
    r = ex.execute("c1", "not_exist", {})
    assert not r.ok and r.error_code == ToolResultErrorCode.UNKNOWN_TOOL.value


def test_invalid_args_rejected():
    ex = ToolExecutor(_registry_with(_EchoTool()))
    r = ex.execute("c1", "echo", {"text": "x" * 30})
    assert not r.ok and r.error_code == ToolResultErrorCode.INVALID_ARGS.value


def test_idempotency():
    ex = ToolExecutor(_registry_with(_EchoTool()))
    r1 = ex.execute("c1", "echo", {"text": "a"})
    r2 = ex.execute("c1", "echo", {"text": "b"})
    assert r1.ok and not r2.ok and r2.error_code == ToolResultErrorCode.DUPLICATE_CALL.value


def test_l2_without_approver_denied():
    ex = ToolExecutor(_registry_with(_EchoTool(permission=PermissionLevel.L2)))
    r = ex.execute("c2", "echo", {"text": "a"})
    assert not r.ok and r.error_code == ToolResultErrorCode.PERMISSION_DENIED.value


def test_l2_approval_allowed_once():
    ex = ToolExecutor(_registry_with(_EchoTool(permission=PermissionLevel.L2)))
    r = ex.execute("c3", "echo", {"text": "a"},
                   approver=_Approver(ApprovalOutcome.ALLOWED_ONCE))
    assert r.ok and r.content == "a"


def test_l2_approval_rejected():
    ex = ToolExecutor(_registry_with(_EchoTool(permission=PermissionLevel.L2)))
    r = ex.execute("c4", "echo", {"text": "a"},
                   approver=_Approver(ApprovalOutcome.REJECTED))
    assert not r.ok and r.error_code == ToolResultErrorCode.PERMISSION_DENIED.value


def test_approval_exception_fail_closed():
    class _Broken:
        def ask(self, **kwargs):
            raise RuntimeError("审批器崩了")

    ex = ToolExecutor(_registry_with(_EchoTool(permission=PermissionLevel.L2)))
    r = ex.execute("c5", "echo", {"text": "a"}, approver=_Broken())
    assert not r.ok and r.error_code == ToolResultErrorCode.PERMISSION_DENIED.value


def test_timeout():
    ex = ToolExecutor(_registry_with(_EchoTool(delay=2.0)))
    t0 = time.monotonic()
    r = ex.execute("c6", "echo", {"text": "a"})
    assert not r.ok and r.error_code == ToolResultErrorCode.TIMEOUT.value
    assert time.monotonic() - t0 < 1.5  # 0.4s 超时生效


def test_tool_exception_converged():
    ex = ToolExecutor(_registry_with(_EchoTool(raise_exc=True)))
    r = ex.execute("c7", "echo", {"text": "a"})
    assert not r.ok and r.error_code == ToolResultErrorCode.EXECUTION_ERROR.value


def test_cancel_before_execute():
    ex = ToolExecutor(_registry_with(_EchoTool()))
    ev = threading.Event()
    ev.set()
    r = ex.execute("c8", "echo", {"text": "a"}, cancel=ev)
    assert not r.ok and r.error_code == ToolResultErrorCode.EXECUTION_ERROR.value


# ---------- permissions ----------

def test_permission_levels():
    pm = PermissionManager()
    spec_l0 = _EchoTool().spec
    assert pm.check(spec_l0, {}).allowed
    spec_l2 = _EchoTool(permission=PermissionLevel.L2).spec
    assert not pm.check(spec_l2, {}).allowed
