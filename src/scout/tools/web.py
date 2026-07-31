"""网页抓取：把 URL 变成可引用的证据。

``fetch_url`` 抓完会自动把正文切块入证据库并分配 [S1] 这样的引用标签，
所以模型只要抓过，后面写报告时就能引用它——不需要额外一步"保存证据"。
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from typing import Annotated

import httpx

from .base import Risk, ToolContext, ToolResult, tool

USER_AGENT = "Mozilla/5.0 (compatible; scout-agent/0.1; +https://github.com/)"
PREVIEW_CHARS = 6000

_SCRIPT_STYLE = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_BLOCK_END = re.compile(r"</(p|div|li|h[1-6]|tr|section|article)>|<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n{3,}")


@dataclass(slots=True)
class Page:
    title: str
    text: str


def html_to_page(raw: str) -> Page:
    """极简正文提取：去脚本样式、块级标签转换行、剥标签、压空行。

    没上 readability/trafilatura 是刻意的：多一个重依赖，
    而调研场景下模型对少量导航噪音的容忍度其实很高。
    真要提升正文质量，替换这一个函数即可。
    """
    title_match = _TITLE.search(raw)
    title = html_lib.unescape(_TAG.sub("", title_match.group(1))).strip() if title_match else ""

    body = _SCRIPT_STYLE.sub(" ", raw)
    body = _BLOCK_END.sub("\n", body)
    body = _TAG.sub("", body)
    body = html_lib.unescape(body)
    body = "\n".join(line.strip() for line in body.splitlines())
    body = _BLANK_LINES.sub("\n\n", body).strip()
    return Page(title=title, text=body)


@tool(risk=Risk.SAFE)
def fetch_url(
    ctx: ToolContext,
    url: Annotated[str, "要抓取的完整 URL，必须以 http:// 或 https:// 开头"],
    preview_chars: Annotated[int, "返回给你的正文预览长度，全文已完整入库"] = PREVIEW_CHARS,
) -> ToolResult:
    """抓取网页正文并存入证据库，返回引用标签和正文预览。

    抓过的页面会分配 [S1]/[S2] 这样的标签，写报告时用这个标签标注出处。
    正文全文已入库，即使这里只给你预览，之后也能用 search_evidence 检索到细节。
    """
    if not url.startswith(("http://", "https://")):
        return ToolResult.failure("URL 必须以 http:// 或 https:// 开头")

    try:
        response = httpx.get(
            url,
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return ToolResult.failure(f"抓取失败：{exc}。可以换一个来源，或先用 web_search 找替代链接")

    content_type = response.headers.get("content-type", "")
    if "html" in content_type:
        page = html_to_page(response.text)
    else:
        page = Page(title=url.rsplit("/", 1)[-1], text=response.text)

    if len(page.text) < 80:
        return ToolResult.failure(
            f"{url} 正文过短（{len(page.text)} 字符），可能是动态渲染页或反爬拦截，建议换来源"
        )

    label = ""
    chunks = 0
    if ctx.evidence is not None:
        label, chunks, is_new = ctx.evidence.ingest(url, page.title or url, page.text)
        if not is_new:
            hint = f"[{label}] 这个页面之前已经抓过并入库，直接引用即可。"
            return ToolResult.success(
                f"{hint}\n标题：{page.title}\n正文预览：\n{page.text[:2000]}",
                display=f"命中缓存 [{label}] {page.title[:40]}",
            )

    header = f"[{label}] " if label else ""
    preview = page.text[: max(500, preview_chars)]
    truncated = "\n...[正文较长，此处截断；完整内容已入证据库，可用 search_evidence 精确检索]" if (
        len(page.text) > preview_chars
    ) else ""
    return ToolResult.success(
        f"{header}标题：{page.title}\nURL：{url}\n入库 {chunks} 个片段\n\n{preview}{truncated}",
        display=f"抓取 [{label or '-'}] {page.title[:40] or url[:40]}",
        label=label,
        chars=len(page.text),
    )


@tool(risk=Risk.CAUTION)
def http_request(
    url: Annotated[str, "接口地址"],
    method: Annotated[str, "HTTP 方法：GET/POST/PUT/DELETE"] = "GET",
    headers: Annotated[dict | None, "请求头，JSON 对象"] = None,
    body: Annotated[str, "请求体，JSON 字符串"] = "",
) -> ToolResult:
    """调用任意 HTTP 接口并返回状态码与响应体，用于对接需要参数的开放 API。"""
    try:
        response = httpx.request(
            method.upper(),
            url,
            headers=headers or {},
            content=body.encode("utf-8") if body else None,
            timeout=30,
            follow_redirects=True,
        )
    except httpx.HTTPError as exc:
        return ToolResult.failure(f"请求失败：{exc}")
    return ToolResult.success(
        f"status={response.status_code}\n{response.text[:8000]}",
        display=f"{method.upper()} {url} → {response.status_code}",
    )


WEB_TOOLS = [fetch_url, http_request]
