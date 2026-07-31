"""子 Agent 委派工具。

为什么需要子 Agent：一个子问题往往要搜 3 次、抓 5 个网页，几万 token 的原始
资料如果全进主上下文，主 Agent 很快就"失忆"了。派一个子 Agent 去查，它在
自己独立的上下文里翻完所有资料，只把一段结论带回来——**主上下文只承担摘要的
成本，证据则沉淀在共享的证据库里**。这是长任务能跑下去的关键结构。
"""

from __future__ import annotations

from typing import Annotated

from .base import Risk, ToolContext, ToolResult, tool


@tool(risk=Risk.SAFE, concurrency_safe=True)
def research_subtopic(
    ctx: ToolContext,
    topic: Annotated[str, "交给子调研员的子课题，要具体、可独立完成"],
    questions: Annotated[list[str], "希望它回答的具体问题清单"] = [],
) -> ToolResult:
    """派一个子调研员独立完成某个子课题，返回它的结论摘要。

    适合：需要大量搜索和阅读、但只需要一段结论的子问题。
    在同一轮里可以同时派多个子调研员，它们会并行工作。
    注意：子调研员看不到你的上下文，topic 里要把背景交代清楚。
    """
    if ctx.spawn is None:
        return ToolResult.failure("当前运行环境不支持子 Agent")

    session = ctx.session
    limit = getattr(ctx.settings, "max_subagents", 3)
    used = getattr(session, "subagents_used", 0)
    if used >= limit:
        return ToolResult.failure(
            f"子调研员配额已用完（上限 {limit} 个）。请基于已有资料自己完成剩余分析。"
        )
    if session is not None:
        session.subagents_used = used + 1

    brief = topic.strip()
    if questions:
        brief += "\n\n需要回答的问题：\n" + "\n".join(f"- {q}" for q in questions if q.strip())

    summary = ctx.spawn(brief)
    return ToolResult.success(
        f"子调研员关于「{topic}」的结论：\n\n{summary}",
        display=f"子调研员完成：{topic[:40]}",
    )


DELEGATE_TOOLS = [research_subtopic]
