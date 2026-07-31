"""本地文件工具：读取本机资料、把中间产物落盘。

调研的输入不总是网页——也可能是本地的 PDF 转出的文本、导出的会议纪要。
路径解析统一走 ``ctx.resolve``，禁止逃出工作区。
"""

from __future__ import annotations

from typing import Annotated

from .base import Risk, ToolContext, ToolResult, tool

IGNORED = {".git", "node_modules", "__pycache__", ".venv", "venv", ".scout", "dist", "build"}


@tool(risk=Risk.SAFE)
def read_file(
    ctx: ToolContext,
    path: Annotated[str, "文件路径，相对工作区或绝对路径"],
    offset: Annotated[int, "从第几行开始读，1 起算"] = 1,
    limit: Annotated[int, "最多读取多少行"] = 400,
) -> ToolResult:
    """读取本地文本文件。文件很长时用 offset/limit 分页读。"""
    target = ctx.resolve(path)
    if not target.exists():
        return ToolResult.failure(f"文件不存在：{path}")
    if target.is_dir():
        return ToolResult.failure(f"{path} 是目录，请用 list_dir")

    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(offset - 1, 0)
    chunk = lines[start : start + max(limit, 1)]
    body = "\n".join(f"{start + i + 1:>5}| {line}" for i, line in enumerate(chunk))
    remaining = len(lines) - start - len(chunk)
    if remaining > 0:
        body += f"\n...还有 {remaining} 行，可用 offset={start + len(chunk) + 1} 继续读"
    return ToolResult.success(body, display=f"读取 {path}（{len(chunk)}/{len(lines)} 行）")


@tool(risk=Risk.SAFE)
def list_dir(
    ctx: ToolContext,
    path: Annotated[str, "目录路径，默认工作区根目录"] = ".",
) -> ToolResult:
    """列出目录内容，自动跳过 .git / node_modules 等噪音目录。"""
    root = ctx.resolve(path)
    if not root.is_dir():
        return ToolResult.failure(f"{path} 不是目录")
    entries = [
        f"{child.name}/" if child.is_dir() else f"{child.name}  ({child.stat().st_size} B)"
        for child in sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
        if child.name not in IGNORED and not child.name.startswith(".")
    ]
    if not entries:
        return ToolResult.success(f"{path} 是空目录")
    return ToolResult.success("\n".join(entries), display=f"列出 {path}（{len(entries)} 项）")


@tool(risk=Risk.CAUTION, concurrency_safe=False)
def write_file(
    ctx: ToolContext,
    path: Annotated[str, "目标文件路径"],
    content: Annotated[str, "完整文件内容，会覆盖原文件"],
) -> ToolResult:
    """写入文本文件（父目录自动创建）。正式报告请用 write_report。"""
    target = ctx.resolve(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    action = "覆盖" if target.exists() else "创建"
    target.write_text(content, encoding="utf-8")
    return ToolResult.success(f"已{action} {path}", display=f"{action} {path}")


FILE_TOOLS = [read_file, list_dir, write_file]
