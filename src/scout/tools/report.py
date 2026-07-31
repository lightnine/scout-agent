"""报告产出工具：把调研结论落成带引用的 Markdown 文件。"""

from __future__ import annotations

import re
import time
from typing import Annotated

from .base import Risk, ToolContext, ToolResult, tool

CITATION = re.compile(r"\[S\d+\]")


@tool(risk=Risk.CAUTION, concurrency_safe=False)
def write_report(
    ctx: ToolContext,
    title: Annotated[str, "报告标题"],
    content: Annotated[str, "Markdown 正文。关键结论后面用 [S1] 这样的标签标注来源"],
    filename: Annotated[str, "文件名（不含扩展名），留空则按标题和日期自动生成"] = "",
) -> ToolResult:
    """把调研报告写入 reports/ 目录，自动追加参考来源清单。

    正文里的每个关键结论都应该带 [S1] 形式的来源标签；没有来源支撑的推测，
    请显式写明"这是推测"。系统会检查引用覆盖情况并提示你。
    """
    if not content.strip():
        return ToolResult.failure("报告正文不能为空")

    reports_dir = ctx.workspace / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    stem = _slugify(filename or title) or "report"
    path = reports_dir / f"{time.strftime('%Y%m%d-%H%M')}-{stem}.md"

    bibliography = ctx.evidence.bibliography() if ctx.evidence is not None else ""
    body = content.strip()
    # 模型经常在正文里自己写一级标题，不判断就会出现两个 H1
    document = body + "\n" if body.startswith("# ") else f"# {title}\n\n{body}\n"
    if bibliography and "## 参考来源" not in content:
        document += f"\n---\n\n{bibliography}\n"
    path.write_text(document, encoding="utf-8")

    citations = set(CITATION.findall(content))
    total_sources = len(ctx.evidence.sources()) if ctx.evidence is not None else 0
    notice = ""
    if total_sources and not citations:
        notice = "\n注意：正文没有任何 [S*] 引用标记，结论无法追溯，建议补充来源标注。"
    elif total_sources:
        notice = f"\n引用覆盖：正文引用了 {len(citations)}/{total_sources} 个来源。"

    return ToolResult.success(
        f"报告已写入 {path.relative_to(ctx.workspace)}（{len(document)} 字符）{notice}",
        display=f"生成报告 {path.name}",
        path=str(path),
    )


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", text.strip())
    return text.strip("-")[:40]


REPORT_TOOLS = [write_report]
