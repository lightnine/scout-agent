"""证据库相关工具：从已抓取的资料里精确检索、查看来源清单。"""

from __future__ import annotations

from typing import Annotated

from .base import Risk, ToolContext, ToolResult, tool


@tool(risk=Risk.SAFE)
def search_evidence(
    ctx: ToolContext,
    query: Annotated[str, "要在已抓取资料中查找的内容，用自然语言描述"],
    limit: Annotated[int, "返回片段数量"] = 5,
) -> ToolResult:
    """在**已经抓取过**的网页正文里做语义检索，返回带来源标签的原文片段。

    抓取时正文只给了你预览，细节都在库里。要核对具体数字、日期、原话时用这个，
    比重新抓一遍整页省 token，也更精准。
    """
    if ctx.evidence is None:
        return ToolResult.failure("当前会话没有证据库")
    hits = ctx.evidence.search(query, limit=max(1, min(limit, 10)))
    if not hits:
        total = ctx.evidence.count()
        if total == 0:
            return ToolResult.success("证据库还是空的，先用 web_search + fetch_url 收集资料。")
        return ToolResult.success(
            f"证据库里有 {total} 个片段，但没有与「{query}」相关的内容。"
            "可以换个说法检索，或者去搜集新的资料。"
        )
    body = "\n\n---\n\n".join(h.render() for h in hits)
    return ToolResult.success(body, display=f"检索证据「{query}」→ {len(hits)} 段")


@tool(risk=Risk.SAFE)
def list_sources(ctx: ToolContext) -> ToolResult:
    """列出本次调研已收录的全部来源及其引用标签，写报告标注出处前先看一眼。"""
    if ctx.evidence is None:
        return ToolResult.failure("当前会话没有证据库")
    sources = ctx.evidence.sources()
    if not sources:
        return ToolResult.success("还没有收录任何来源。")
    body = "\n".join(f"[{s['label']}] {s['title'] or '(无标题)'}\n    {s['url']}" for s in sources)
    return ToolResult.success(body, display=f"共 {len(sources)} 个来源")


EVIDENCE_TOOLS = [search_evidence, list_sources]
