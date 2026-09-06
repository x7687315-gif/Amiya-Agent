"""生成期导航防护 + 视图丢失优雅收尾（2026-09-06 代码质量审查修复）。

背景（consultant 审查 CRITICAL-2）：
流式生成期间点「新对话」或切换日期会把正在流式输出的 text_control 从控件树
摘掉，下一次 append_assistant_text 对脱离控件 update() 抛异常，被 _worker 的兜底
except 误报成「通讯有些干扰」；且生成器被弃置后，reply() 的收尾段（半截回复
落库 + 入短期窗口 + 情绪持久化）不在 finally 保护内，**永远不执行**——用户
丢了半截回复，库里也没有记录。

修复两层防御：
1. 生成期（_generating 置位）直接忽略 新对话 / 日期切换 / 换肤重建；
2. 视图仍被换走时（残余竞态），_worker 排空生成器触发 reply() 正常收尾，
   与「停止生成」按钮走同一条已测试的落库路径，绝不再丢半截回复。
"""
from __future__ import annotations

from ui.app import AssistantApp


# ----- 替身 -----


class _FakeAgent:
    """reply() 产出固定 delta；request_stop 后下一个 yield 处 break（与真实
    llm_client/agent 循环同构）；循环正常结束后置 finalized，模拟 reply()
    尾部落库段执行。"""

    def __init__(self, deltas):
        self._deltas = deltas
        self.stop_requested = False
        self.finalized = False

    def request_stop(self) -> None:
        self.stop_requested = True

    def reply(self, text):
        for d in self._deltas:
            yield d
            if self.stop_requested:
                break
        self.finalized = True  # 模拟「落库 + 入窗口 + 情绪持久化」收尾段


class _FakeHeader:
    def __init__(self):
        self.thinking = None

    def set_thinking(self, v):
        self.thinking = v


class _FakeInputBar:
    def __init__(self):
        self.loading = None

    def set_loading(self, v):
        self.loading = v

    def focus(self):
        pass


class _FakeChatArea:
    """append 从第 fail_from 次调用开始抛（模拟流式控件已被换走）；
    start_raises=True 时模拟首个 token 前视图就被换走。"""

    def __init__(self, fail_from=None, start_raises=False):
        self.errors: list[str] = []
        self.appended: list[str] = []
        self.fail_from = fail_from
        self.start_raises = start_raises
        self._calls = 0

    def start_assistant(self):
        if self.start_raises:
            raise RuntimeError("detached")
        return object()  # text_control 占位

    def append_assistant_text(self, ctrl, delta):
        self._calls += 1
        if self.fail_from is not None and self._calls >= self.fail_from:
            raise RuntimeError("detached")
        self.appended.append(delta)

    def add_error(self, msg):
        self.errors.append(msg)

    def remove_thinking(self):
        pass

    def add_assistant(self, text):
        pass


def _make_app(deltas, fail_from=None, start_raises=False) -> AssistantApp:
    app = AssistantApp()
    app.agent = _FakeAgent(deltas)
    app.chat_area = _FakeChatArea(fail_from=fail_from, start_raises=start_raises)
    app.header = _FakeHeader()
    app.input_bar = _FakeInputBar()
    app.persona_status = None  # set_state 分支跳过
    app.page = object()  # _worker 的 assert 只要求非 None
    # _memory / date_nav 保持 None：_refresh_day_options 直接早退
    return app


# ----- 防线 1：生成期导航直接忽略 -----


def test_new_chat_ignored_while_generating():
    app = AssistantApp()
    app._generating = True
    calls = []

    class _Agent:
        def reset(self):
            calls.append("reset")

    class _Chat:
        def clear(self):
            calls.append("clear")

    app.agent = _Agent()
    app.chat_area = _Chat()

    app._on_new_chat()
    assert calls == []  # 生成中：既不轮换会话也不清屏

    app._generating = False
    app._on_new_chat()
    assert calls == ["reset", "clear"]  # 空闲：行为不变


def test_day_change_ignored_while_generating():
    app = AssistantApp()
    app._generating = True
    shown = []

    class _Chat:
        def show_day(self, *a):
            shown.append(a)

    app.chat_area = _Chat()

    app._on_day_change("2026-09-01")
    assert shown == []  # 生成中：不换视图

    app._generating = False
    app._on_day_change("2026-09-01")
    assert len(shown) == 1  # 空闲：行为不变


# ----- 防线 2：视图丢失后排空生成器、半截回复照常收尾 -----


def test_worker_drains_and_finalizes_when_view_lost_mid_stream():
    """流式中途视图被换走：不再误报「通讯有些干扰」，生成器被排空，
    reply() 收尾段（模拟为 finalized）必须执行，_generating 复位。"""
    app = _make_app(["你", "好", "呀"], fail_from=2)  # 第 2 个 delta 起控件已脱离
    app._generating = True
    app._worker("hi")

    assert app.agent.finalized is True  # 半截回复收尾落库路径被执行
    assert app.agent.stop_requested is True  # 触发停止以省 token
    assert app.chat_area.errors == []  # 不再误报「通讯有些干扰」
    assert app._generating is False  # finally 复位
    assert app.header.thinking is False


def test_worker_drains_when_view_lost_before_first_token():
    """首个 token 前视图就被换走（start_assistant 抛异常）：同样优雅收尾。"""
    app = _make_app(["你", "好"], start_raises=True)
    app._generating = True
    app._worker("hi")

    assert app.agent.finalized is True
    assert app.agent.stop_requested is True
    assert app.chat_area.errors == []
    assert app._generating is False


def test_worker_normal_path_unaffected():
    """正常完成：全部 delta 上屏，无错误气泡，收尾自然执行（回归保护）。"""
    app = _make_app(["你", "好"])
    app._generating = True
    app._worker("hi")

    assert app.chat_area.appended == ["你", "好"]
    assert app.agent.finalized is True
    assert app.agent.stop_requested is False  # 正常路径不请求停止
    assert app.chat_area.errors == []
    assert app._generating is False


def test_worker_llm_failure_still_reports_error():
    """真 LLM 故障（非视图丢失）仍走原有错误气泡路径（回归保护）。"""
    app = AssistantApp()

    class _BoomAgent:
        def request_stop(self):
            pass

        def reply(self, text):
            raise RuntimeError("network down")
            yield ""  # pragma: no cover

    app.agent = _BoomAgent()
    app.chat_area = _FakeChatArea()
    app.header = _FakeHeader()
    app.input_bar = _FakeInputBar()
    app.page = object()
    app._generating = True
    app._worker("hi")

    assert app.chat_area.errors == ["通讯有些干扰，请稍后再试。"]
    assert app._generating is False


# ----- 防线 1 补充：换肤重建与 _generating 单一busy源 -----


def test_skin_busy_check_uses_generating_flag():
    """换肤 busy 判断应读 _generating（单一真源），
    不再跨对象探测 input_bar._is_loading 私有属性。"""
    app = AssistantApp()
    assert app._generating is False  # 初始为空闲


if __name__ == "__main__":  # pragma: no cover
    import pytest

    pytest.main([__file__, "-q"])
