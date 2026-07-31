"""工具层的统一出口。

``build_registry`` 组装主 Agent 的全套工具；子 Agent 用 ``SUBAGENT_TOOLS``
里列出的子集——只给它调研需要的能力，写报告、派子 Agent 这类权限一律不给，
既省 token 也避免子 Agent 递归派生失控。
"""

from __future__ import annotations

from typing import Any

from .base import Risk, Tool, ToolContext, ToolResult, tool
from .delegate import DELEGATE_TOOLS
from .evidence_tools import EVIDENCE_TOOLS
from .files import FILE_TOOLS
from .memory_tools import MEMORY_TOOLS
from .plan import PLAN_TOOLS, Plan
from .registry import ToolRegistry
from .report import REPORT_TOOLS
from .search import SEARCH_TOOLS
from .web import WEB_TOOLS

ALL_TOOLS: list[Tool] = [
    *SEARCH_TOOLS,
    *WEB_TOOLS,
    *EVIDENCE_TOOLS,
    *PLAN_TOOLS,
    *REPORT_TOOLS,
    *MEMORY_TOOLS,
    *FILE_TOOLS,
    *DELEGATE_TOOLS,
]

SUBAGENT_TOOLS = ["web_search", "fetch_url", "search_evidence", "read_file"]


def build_registry(
    ctx: ToolContext, approver: Any = None, max_workers: int = 4
) -> ToolRegistry:
    registry = ToolRegistry(ctx, approver=approver, max_workers=max_workers)
    registry.register_all(ALL_TOOLS)
    return registry


__all__ = [
    "ALL_TOOLS",
    "SUBAGENT_TOOLS",
    "Plan",
    "Risk",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "build_registry",
    "tool",
]
