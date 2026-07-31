"""长期记忆工具：让 Agent 自己决定"什么值得记住"。

只把稳定的事实和偏好写进长期记忆——一次性的、会过期的信息记下来是负债，
下次召回时反而会误导判断。这条约束写在工具描述里，模型看得到。
"""

from __future__ import annotations

from typing import Annotated

from .base import Risk, ToolContext, ToolResult, tool


@tool(risk=Risk.CAUTION)
def remember(
    ctx: ToolContext,
    content: Annotated[str, "要长期记住的一句话，写成独立完整的陈述句"],
    tags: Annotated[str, "逗号分隔的标签，便于后续检索"] = "",
) -> ToolResult:
    """把一条信息写入长期记忆，之后的新会话里会被自动召回。

    适合记：用户的身份/偏好/习惯用语、长期有效的领域结论、反复用到的资料出处。
    不要记：本次任务的中间过程、会过期的时效信息、能随时重新查到的内容。
    """
    if ctx.memory is None:
        return ToolResult.failure("长期记忆不可用")
    session_id = getattr(ctx.session, "id", None)
    memory_id = ctx.memory.save(content, tags, session_id)
    return ToolResult.success(f"已记住（#{memory_id}）：{content}", display=f"记住：{content[:40]}")


@tool(risk=Risk.SAFE)
def recall(
    ctx: ToolContext,
    query: Annotated[str, "想回忆的内容"],
    limit: Annotated[int, "最多返回几条"] = 5,
) -> ToolResult:
    """主动检索长期记忆。每轮开始时系统已自动召回一批，这里用于补充查询。"""
    if ctx.memory is None:
        return ToolResult.failure("长期记忆不可用")
    hits = ctx.memory.search(query, limit=max(1, min(limit, 10)))
    if not hits:
        return ToolResult.success(f"没有与「{query}」相关的长期记忆。")
    body = "\n".join(f"- (#{h.id}, 相关度 {h.score:.2f}) {h.content}" for h in hits)
    return ToolResult.success(body, display=f"召回 {len(hits)} 条记忆")


MEMORY_TOOLS = [remember, recall]
