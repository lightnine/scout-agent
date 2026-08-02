"""工具注册表：查找、鉴权、执行（含并发与结果截断）。"""

from __future__ import annotations

import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..cancellation import RunCancelled
from ..llm.base import ToolCall
from ..llm.cache import stable_tool_schemas
from .base import Tool, ToolContext, ToolResult

MAX_RESULT_CHARS = 20000


class ToolRegistry:
    def __init__(self, ctx: ToolContext, approver: Any = None, max_workers: int = 4) -> None:
        self.ctx = ctx
        self.approver = approver
        self.max_workers = max_workers
        self._tools: dict[str, Tool] = {}
        self._cached_schemas: list[dict[str, Any]] | None = None

    # ------------------------------------------------------------ 注册/查询
    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"工具重名：{tool.name}")
        self._tools[tool.name] = tool
        self._cached_schemas = None

    def register_all(self, tools: Iterable[Tool]) -> None:
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def subset(self, names: Iterable[str]) -> ToolRegistry:
        """派生一个只含指定工具的注册表，用于限制子 Agent 的能力边界。"""
        child = ToolRegistry(self.ctx, self.approver, self.max_workers)
        for name in names:
            tool = self._tools.get(name)
            if tool is not None:
                child.register(tool)
        return child

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [t.to_openai_schema() for t in self._tools.values()]

    def cached_schemas(self) -> list[dict[str, Any]]:
        """整轮 run 内 memoize + 按名称排序，作为独立的 tools cache block 发送。"""
        if self._cached_schemas is None:
            self._cached_schemas = stable_tool_schemas(self.schemas())
        return self._cached_schemas

    # ---------------------------------------------------------------- 执行
    def execute(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.name)
        if tool is None:
            available = ", ".join(sorted(self._tools)) or "（无）"
            return ToolResult.failure(f"不存在名为 {call.name} 的工具。当前可用：{available}")

        if self.approver is not None:
            session_id = getattr(self.ctx.session, "id", "")
            decision = self.approver.check(
                tool,
                call.arguments,
                session_id=session_id,
                run_id=self.ctx.run_id,
            )
            if not decision.allowed:
                return ToolResult.failure(decision.reason or "该操作未获批准")

        started = time.monotonic()
        result = tool.run(self.ctx, **call.arguments)
        result.meta["duration_ms"] = int((time.monotonic() - started) * 1000)
        result.meta["tool"] = tool.name
        return _clip(result)

    def execute_batch(self, calls: list[ToolCall]) -> list[ToolResult]:
        """一轮里模型可能同时发起多个工具调用。

        全部只读时并行执行（调研场景收益很大：一次并发抓 5 个网页）；
        只要有一个带副作用，就退化为串行，避免难以复现的竞态。
        """
        all_safe = all(
            (t := self._tools.get(c.name)) is not None and t.concurrency_safe for c in calls
        )
        if len(calls) <= 1 or not all_safe:
            results: list[ToolResult] = []
            for index, call in enumerate(calls):
                try:
                    results.append(self.execute(call))
                except RunCancelled:
                    if self.ctx.cancellation is not None:
                        self.ctx.cancellation.request()
                    results.extend(
                        ToolResult.failure("运行已取消；工具未执行。")
                        for _ in calls[index:]
                    )
                    break
            return results

        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(calls))) as pool:
            return list(pool.map(self.execute, calls))


def _clip(result: ToolResult) -> ToolResult:
    """工具输出直接进上下文，必须限长，否则一次 grep 就能撑爆 window。"""
    if len(result.content) <= MAX_RESULT_CHARS:
        return result
    keep = MAX_RESULT_CHARS // 2
    omitted = len(result.content) - MAX_RESULT_CHARS
    result.content = (
        result.content[:keep]
        + f"\n\n...[内容过长，中间省略 {omitted} 字符]...\n\n"
        + result.content[-keep:]
    )
    result.meta["truncated"] = True
    return result
