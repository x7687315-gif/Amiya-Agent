"""Application Layer（2.x Phase 3C）：统一入口与请求分流。

契约：ARCHITECTURE_2.0.md §3.2-§3.4 · INTERFACE_CONTRACTS_2.0.md §1-§2。
Router 只用三个可观测信号分流，不猜语义、不引分类器模型（简洁性预算）。
"""
from .router import Handoff, RouteDecision, RequestRouter
from .facade import RuntimeStack, build_runtime_stack
from .approval import ApprovalBridge, ApprovalRequest
from .runner import TaskRunner
from .research import ResearchCall, ResearchResult, ResearchRunner, ResearchSource

__all__ = [
    "Handoff", "RouteDecision", "RequestRouter",
    "RuntimeStack", "build_runtime_stack",
    "ApprovalBridge", "ApprovalRequest",
    "TaskRunner",
    "ResearchRunner", "ResearchResult", "ResearchSource", "ResearchCall",
]
