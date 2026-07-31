"""搜索工具：三个可插拔的搜索后端。

- ``duckduckgo``：解析 HTML 版搜索页，**不需要任何 API Key**，开箱即用
- ``tavily``：专为 LLM 设计的搜索 API，返回的正文摘要质量最好
- ``serper``：Google 搜索结果，中文长尾问题覆盖更全

解析逻辑都写成纯函数（``parse_*``），单测可以喂固定 HTML/JSON 离线验证，
不依赖网络——爬虫类代码最容易腐烂，没有回归测试根本不敢改。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .base import Risk, ToolContext, ToolResult, tool

DDG_ENDPOINT = "https://html.duckduckgo.com/html/"
DDG_LITE_ENDPOINT = "https://lite.duckduckgo.com/lite/"
TAVILY_ENDPOINT = "https://api.tavily.com/search"
SERPER_ENDPOINT = "https://google.serper.dev/search"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_RESULT_LINK = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S
)
_SNIPPET = re.compile(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S)
# lite 版是表格布局，class 用单引号，且 href 在 class 之前
_LITE_LINK = re.compile(r"<a[^>]+href=\"([^\"]+)\"[^>]*class='result-link'[^>]*>(.*?)</a>", re.S)
_LITE_SNIPPET = re.compile(r"<td[^>]*class='result-snippet'[^>]*>(.*?)</td>", re.S)
_TAGS = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str

    def render(self, index: int) -> str:
        return f"{index}. {self.title}\n   {self.url}\n   {self.snippet}"


# ------------------------------------------------------------------ 解析函数
def parse_duckduckgo(html_text: str, limit: int = 8) -> list[SearchResult]:
    links = _RESULT_LINK.findall(html_text)
    snippets = _SNIPPET.findall(html_text)
    results: list[SearchResult] = []
    for index, (href, title_html) in enumerate(links[:limit]):
        snippet = _strip(snippets[index]) if index < len(snippets) else ""
        results.append(SearchResult(_strip(title_html), _unwrap_ddg(href), snippet))
    return results


def parse_duckduckgo_lite(html_text: str, limit: int = 8) -> list[SearchResult]:
    links = _LITE_LINK.findall(html_text)
    snippets = _LITE_SNIPPET.findall(html_text)
    return [
        SearchResult(
            _strip(title_html),
            _unwrap_ddg(href),
            _strip(snippets[index]) if index < len(snippets) else "",
        )
        for index, (href, title_html) in enumerate(links[:limit])
    ]


def parse_tavily(payload: dict[str, Any], limit: int = 8) -> list[SearchResult]:
    return [
        SearchResult(item.get("title", ""), item.get("url", ""), (item.get("content") or "")[:400])
        for item in (payload.get("results") or [])[:limit]
    ]


def parse_serper(payload: dict[str, Any], limit: int = 8) -> list[SearchResult]:
    return [
        SearchResult(item.get("title", ""), item.get("link", ""), item.get("snippet", ""))
        for item in (payload.get("organic") or [])[:limit]
    ]


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo 的结果链接是跳转包装，真实地址在 uddg 参数里。"""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])
    return href


def _strip(fragment: str) -> str:
    return html.unescape(_TAGS.sub("", fragment)).strip()


# ---------------------------------------------------------------------- 工具
@tool(risk=Risk.SAFE)
def web_search(
    ctx: ToolContext,
    query: Annotated[str, "搜索关键词。一次只查一个具体问题，别把多个问题塞进一个 query"],
    limit: Annotated[int, "返回结果条数，1~10"] = 6,
) -> ToolResult:
    """联网搜索，返回标题、URL 和摘要片段。

    搜索结果只是线索，摘要往往不足以支撑结论；确认某条结果有价值后，
    应当用 fetch_url 抓取正文，才能进入证据库并被引用。
    """
    query = query.strip()
    if not query:
        return ToolResult.failure("搜索词不能为空")
    limit = max(1, min(limit, 10))

    provider, notice = resolve_provider(ctx.settings)
    try:
        if provider == "tavily":
            results = _search_tavily(ctx.settings.tavily_api_key, query, limit)
        elif provider == "serper":
            results = _search_serper(ctx.settings.serper_api_key, query, limit)
        else:
            results = _search_duckduckgo(query, limit)
    except httpx.HTTPError as exc:
        return ToolResult.failure(f"搜索请求失败（provider={provider}）：{exc}{notice}")

    if not results:
        return ToolResult.success(
            f"「{query}」没有搜到结果。可以换个说法：拆得更细、去掉限定词，或改用英文关键词。{notice}",
            display=f"搜索「{query}」无结果",
        )

    body = "\n".join(r.render(i) for i, r in enumerate(results, 1))
    return ToolResult.success(
        f"「{query}」的搜索结果（provider={provider}）：\n{body}{notice}",
        display=f"搜索「{query}」→ {len(results)} 条",
        count=len(results),
        provider=provider,
    )


def resolve_provider(settings: Any) -> tuple[str, str]:
    """确定实际使用的搜索后端，返回 (provider, 提示语)。

    配置了 tavily/serper 却没填 Key 时，退回 DuckDuckGo 但**必须说出来**——
    静默降级会让人以为付费后端已经生效，排查体验非常糟糕。
    """
    provider = getattr(settings, "search_provider", "duckduckgo") or "duckduckgo"
    keys = {"tavily": "tavily_api_key", "serper": "serper_api_key"}

    if provider not in keys:
        return "duckduckgo", ""
    if getattr(settings, keys[provider], ""):
        return provider, ""
    return "duckduckgo", (
        f"\n\n[提示] SEARCH_PROVIDER 配置为 {provider}，但 .env 里没有填 "
        f"{keys[provider].upper()}，本次已退回 DuckDuckGo。"
    )


def _search_duckduckgo(query: str, limit: int) -> list[SearchResult]:
    """先打 HTML 版，拿不到结果再打 lite 版。

    DuckDuckGo 会不定时对无头请求返回 202 挑战页（内容是正常 HTML，只是没有结果），
    单一端点因此不可靠。两个端点的反爬策略不同，互为兜底后成功率明显更高。
    """
    attempts = (
        (DDG_ENDPOINT, parse_duckduckgo, {"q": query, "kl": "wt-wt"}),
        (DDG_LITE_ENDPOINT, parse_duckduckgo_lite, {"q": query}),
    )
    last_error: Exception | None = None
    for endpoint, parser, payload in attempts:
        try:
            # 不要手动设置 Content-Type：httpx 用 data= 时会自己生成，
            # 手写反而会被 DDG 的反爬识别，返回 202 挑战页（血泪教训）
            response = httpx.post(
                endpoint,
                data=payload,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                },
                timeout=30,
                follow_redirects=True,
            )
            response.raise_for_status()
            results = parser(response.text, limit)
            if results:
                return results
        except httpx.HTTPError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return []


def _search_tavily(api_key: str, query: str, limit: int) -> list[SearchResult]:
    response = httpx.post(
        TAVILY_ENDPOINT,
        json={
            "api_key": api_key,
            "query": query,
            "max_results": limit,
            "search_depth": "basic",
        },
        timeout=30,
    )
    response.raise_for_status()
    return parse_tavily(response.json(), limit)


def _search_serper(api_key: str, query: str, limit: int) -> list[SearchResult]:
    response = httpx.post(
        SERPER_ENDPOINT,
        json={"q": query, "num": limit},
        headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    return parse_serper(response.json(), limit)


SEARCH_TOOLS = [web_search]
