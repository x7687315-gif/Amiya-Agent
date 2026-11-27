"""ResearchRunner：一次「联网研究」的执行与结果整理（2.x Phase 4B）。

与 TaskRunner 的分工：
- TaskRunner 走本地工具循环（`chat_with_tools` + ToolExecutor），**不能**带服务端工具；
- ResearchRunner 走 DeepSeek 服务端工具（`chat_with_server_tools`），**不碰**本地 Executor。
两者互斥是官方协议约束（同一请求混用会 400），不是设计偏好。

边界（红线）：
- 服务端工具只由 DeepSeek 执行；本模块**不做任何工具执行**。
- 返回的网页内容只用于「展示与引用」，绝不写入 Knowledge/Memory（沿用 Memory Gate：
  外部内容要入库必须经用户确认，Phase 4 不自动入库）。
- 单轮、非流式：没有中间进度就不假装有（UI 只显示「正在检索」）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

from core.agent import Agent
from core.llm_client import LLMError, LLMResponse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResearchSource:
    """一条可展示的来源。不抓正文、不做摘要——正文在服务端 signature 里。"""

    url: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class ResearchCall:
    """服务端一次工具调用的可读投影（UI 按此渲染一行）。"""

    kind: str          # search | read_link | open_page | find_in_page | deep_search | ...
    label: str         # 人话："搜索：上海天气" / "读取：https://..."
    ok: bool = True
    detail: str = ""   # "3 条结果" / 错误码


@dataclass(frozen=True)
class ResearchResult:
    ok: bool
    text: str = ""
    sources: List[ResearchSource] = field(default_factory=list)
    calls: List[ResearchCall] = field(default_factory=list)
    error: str = ""
    error_kind: str = ""


_KIND_LABEL = {
    "search": "搜索",
    "deep_search": "深度搜索",
    "image_search": "图片搜索",
    "deep_image_search": "深度图片搜索",
    "read_link": "读取链接",
    "open_page": "打开页面",
    "find_in_page": "页内查找",
}

# 联网轮次的追加规则（拼在 persona system prompt 之后）。
#
# 为什么必须有它（2026-11-25 实机 + 对照实验证据）：
# 同一句「明天湛江天气」，极简 system → 模型搜索 1 次、拿到 11 条来源、答对；
# 换成完整助手人格 → **0 次调用**，回答「助手这边没有那边的气象信息」，
# 还编出「天灾预警 / 源石浓度」。即：检索链路没坏，是人格提示让模型判断
# 「助手不该知道现实世界的事」，于是宁可不查也不出戏。
# 官方规定是否搜索由模型自行决定、tool_choice 不能强制，所以只能在提示词里
# 明确"本轮允许并应当查资料"。
RESEARCH_RULES = (
    "【本轮为联网检索模式】\n"
    "用户问的是现实世界的事实。遇到天气、日期、新闻、价格、赛程、版本更新、"
    "地点等需要最新信息的问题，**必须先调用 deepseek_web_search 检索**，"
    "再依据检索结果回答。\n"
    "此时「设定上没有 / 助手不该知道」不适用——你正在查资料，可以知道。"
    "仍然用助手的语气与称呼说话，但事实部分以检索结果为准。\n"
    "检索不到就如实说查不到，不要编造，也不要用世界观设定回避问题。\n"
    "输出是纯文本聊天框：不要用 Markdown 标记（** 加粗、# 标题、反引号、表格），"
    "需要列举时用短句分行即可。"
)

# 错误 kind → 用户可读的一句话。原始报文（含 request_id、JSON）只进日志，
# 不甩进聊天气泡——那是给开发者看的，不是给用户看的。
_ERROR_HINTS = {
    "auth": "API Key 无效或已过期",
    "quota": "账户余额不足",
    "rate_limit": "请求太频繁，请稍后再试",
    "timeout": "联网检索超时",
    "network": "网络连接失败",
    "server": "服务端暂时不可用",
    "protocol": "服务端拒绝了这次请求（详情见日志）",
    "content_filter": "回答被安全审核拦截",
    "empty": "没有拿到可用的回答",
    "busy": "已有联网检索在进行",
}


def friendly_error(kind: str, detail: str = "") -> str:
    """把错误分类翻译成用户能懂的一句话；未分类时退回截断后的原文。"""
    hint = _ERROR_HINTS.get(kind or "")
    if hint:
        return hint
    return (detail or "联网检索失败")[:80]


def _call_to_row(call: dict) -> ResearchCall:
    """把一条 server_tool_call 转成一行可读文本（不猜语义，只翻译结构）。"""
    action = call.get("action") or {}
    atype = str(action.get("type") or "")
    kind = str(call.get("kind") or atype or "search")
    ok = str(call.get("status") or "completed") == "completed"

    if atype == "search" or "queries" in action:
        queries = action.get("queries") or []
        label = f"搜索：{'、'.join(str(q) for q in queries[:3])}" if queries else "搜索"
    elif atype in ("read_link", "open_page"):
        label = f"读取：{action.get('url', '')}"
    elif atype == "find_in_page":
        label = f"页内查找：{action.get('pattern', '')}"
    else:
        label = _KIND_LABEL.get(kind, kind or "工具")

    detail = ""
    if not ok:
        err = call.get("error") or {}
        detail = str(err.get("code") or "失败")
    else:
        sources = call.get("sources") or []
        page = call.get("page") or {}
        if sources:
            detail = f"{len(sources)} 条结果"
        elif page.get("url"):
            detail = str(page.get("title") or page.get("url"))[:40]
    return ResearchCall(kind=kind, label=label, ok=ok, detail=detail)


def extract_sources(resp: LLMResponse, *, limit: int = 10) -> List[ResearchSource]:
    """从 server_tool_calls（优先）与 annotations（兜底）收集去重来源。"""
    seen: dict = {}

    def _add(url: str, title: str = "", snippet: str = "") -> None:
        if url and url not in seen:
            seen[url] = ResearchSource(url=url, title=title or "", snippet=snippet or "")

    for step in resp.server_tool_calls or []:
        for call in (step or {}).get("calls") or []:
            for src in call.get("sources") or []:
                _add(str(src.get("url") or ""), str(src.get("title") or ""),
                     str(src.get("snippet") or ""))
            page = call.get("page") or {}
            _add(str(page.get("url") or ""), str(page.get("title") or ""))

    if not seen:  # 只有引用、没有工具块时（部分响应形态）用 annotations 兜底
        for ann in resp.annotations or []:
            uc = (ann or {}).get("url_citation") or {}
            _add(str(uc.get("url") or ""), str(uc.get("title") or ""))

    return list(seen.values())[:limit]


def extract_calls(resp: LLMResponse, *, limit: int = 12) -> List[ResearchCall]:
    rows: List[ResearchCall] = []
    for step in resp.server_tool_calls or []:
        for call in (step or {}).get("calls") or []:
            rows.append(_call_to_row(call))
            if len(rows) >= limit:
                return rows
    return rows


class ResearchRunner:
    """单轮联网研究。与 TaskRunner 一样是「单飞」的（同一时刻一个请求）。"""

    def __init__(self, *, agent: Agent, llm, tools: List[dict]) -> None:
        if not tools:
            raise ValueError("ResearchRunner 需要至少一个服务端工具声明")
        self._agent = agent
        self._llm = llm
        self._tools = list(tools)
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def run(self, user_text: str) -> ResearchResult:
        """执行一次联网研究。所有错误收敛为 ResearchResult，不向 UI 抛。"""
        if self._running:
            return ResearchResult(False, error="已有联网检索在进行", error_kind="busy")
        self._running = True
        try:
            ctx = self._agent.begin_task(user_text)
            try:
                resp = self._llm.chat_with_server_tools(
                    ctx.system_prompt + "\n\n" + RESEARCH_RULES,
                    ctx.messages, tools=self._tools)
            except LLMError as exc:
                log.warning("联网检索失败（kind=%s）：%s", exc.kind, exc)
                return ResearchResult(False, error=str(exc), error_kind=exc.kind)

            text = (resp.content or "").strip()
            sources = extract_sources(resp)
            calls = extract_calls(resp)

            if not text:
                # 官方的两种空回答：content_filter（审核拒绝）/ 其他异常
                if resp.finish_reason == "content_filter":
                    return ResearchResult(False, sources=sources, calls=calls,
                                          error="这条内容被安全审核拦截了。",
                                          error_kind="content_filter")
                return ResearchResult(False, sources=sources, calls=calls,
                                      error="没有拿到可用的回答。", error_kind="empty")

            # 与快路径等价的收尾：落库 + 入窗口 + 情绪持久化 + 完成通知
            self._agent.finish_task(text, emotion=ctx.emotion)
            return ResearchResult(True, text=text, sources=sources, calls=calls)
        finally:
            self._running = False

    def cancel(self) -> bool:
        """非流式请求无法在途打断（requests 阻塞），故不支持取消。

        明确返回 False 而不是假装成功——UI 因此不显示「停止」按钮。
        """
        return False
