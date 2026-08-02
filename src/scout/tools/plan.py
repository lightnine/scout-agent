"""调研计划工具。

计划本身也是一种"外置记忆"：写下来的步骤不会被上下文压缩冲掉，
每一步都会重新注入到 runtime reminder 里，是把长任务拉回正轨的锚点。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from .base import Risk, ToolContext, ToolResult, tool


@dataclass
class Plan:
    steps: list[str] = field(default_factory=list)
    current: int = 0  # 1-based，0 表示尚未开始

    def render(self) -> str:
        if not self.steps:
            return ""
        lines = []
        for index, step in enumerate(self.steps, 1):
            if index < self.current:
                mark = "[x]"
            elif index == self.current:
                mark = "[>]"
            else:
                mark = "[ ]"
            lines.append(f"{mark} {index}. {step}")
        return "\n".join(lines)

    @property
    def done(self) -> bool:
        return bool(self.steps) and self.current > len(self.steps)


@tool(risk=Risk.SAFE, concurrency_safe=False)
def update_plan(
    ctx: ToolContext,
    steps: Annotated[list[str], "完整的调研步骤列表，每次都传全量（可以增删改）"],
    current: Annotated[int, "当前正在执行第几步，从 1 开始；全部完成后传 步数+1"] = 1,
) -> ToolResult:
    """创建或更新调研计划。

    拿到复杂问题的第一件事就是拆成 3~6 个可验证的子问题并调用本工具；
    每完成一步就更新 current，让计划和实际进度保持一致。
    """
    if ctx.session is None:
        return ToolResult.failure("当前没有会话上下文")
    steps = [s.strip() for s in steps if s and s.strip()]
    if not steps:
        return ToolResult.failure("计划至少要有一步")

    ctx.session.plan = Plan(steps=steps, current=max(1, min(current, len(steps) + 1)))
    ctx.session.persist_state()
    if ctx.emit:
        ctx.emit("plan_updated", {"plan": ctx.session.plan.render()})
    return ToolResult.success(
        f"计划已更新：\n{ctx.session.plan.render()}",
        display=f"计划 {ctx.session.plan.current}/{len(steps)} 步",
    )


PLAN_TOOLS = [update_plan]
