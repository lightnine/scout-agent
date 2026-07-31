"""搜索结果与网页正文的解析。

爬虫类代码最容易随对方页面改版而腐烂，用固定样本锁住行为，
以后调整正则时能立刻知道有没有改坏。
"""

from __future__ import annotations

from scout.tools.search import (
    parse_duckduckgo,
    parse_duckduckgo_lite,
    parse_serper,
    parse_tavily,
    resolve_provider,
)
from scout.tools.web import html_to_page

DDG_HTML = """
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fmilvus.io%2Fdocs&amp;rut=abc">
       Milvus <b>向量数据库</b>文档
    </a>
  </h2>
  <a class="result__snippet" href="x">Milvus 是一个开源的向量数据库，支持十亿级检索。</a>
</div>
<div class="result results_links">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a" href="https://qdrant.tech/">Qdrant 官网</a>
  </h2>
  <a class="result__snippet" href="y">Qdrant is a vector search engine &amp; database.</a>
</div>
"""


def test_parse_duckduckgo_unwraps_redirect_and_strips_tags():
    results = parse_duckduckgo(DDG_HTML)
    assert len(results) == 2

    first = results[0]
    assert first.url == "https://milvus.io/docs", "必须还原被包装的真实链接"
    assert first.title == "Milvus 向量数据库文档"
    assert "十亿级检索" in first.snippet

    assert results[1].url == "https://qdrant.tech/"
    assert "&" in results[1].snippet, "HTML 实体应被还原"


def test_parse_duckduckgo_respects_limit():
    assert len(parse_duckduckgo(DDG_HTML, limit=1)) == 1


def test_parse_duckduckgo_on_empty_page():
    assert parse_duckduckgo("<html><body>没有结果</body></html>") == []


DDG_LITE_HTML = """
<table border="0">
  <tr><td valign="top">1.&nbsp;</td>
      <td><a rel="nofollow" href="https://milvus.io/zh" class='result-link'>Milvus | 高性能向量数据库</a></td></tr>
  <tr><td>&nbsp;</td>
      <td class='result-snippet'><b>Milvus</b> 是一个为 GenAI 应用构建的开源向量数据库。</td></tr>
  <tr><td valign="top">2.&nbsp;</td>
      <td><a rel="nofollow" href="https://qdrant.tech/" class='result-link'>Qdrant</a></td></tr>
  <tr><td>&nbsp;</td><td class='result-snippet'>Vector search engine.</td></tr>
</table>
"""


def test_parse_duckduckgo_lite():
    """HTML 版被反爬拦截时会退到 lite 版，两套布局完全不同，都要能解析。"""
    results = parse_duckduckgo_lite(DDG_LITE_HTML)
    assert len(results) == 2
    assert results[0].url == "https://milvus.io/zh"
    assert results[0].title == "Milvus | 高性能向量数据库"
    assert "开源向量数据库" in results[0].snippet
    assert results[1].url == "https://qdrant.tech/"


def test_parse_tavily():
    payload = {
        "results": [
            {"title": "标题 A", "url": "https://a.com", "content": "正文摘要 A"},
            {"title": "标题 B", "url": "https://b.com", "content": "正文摘要 B"},
        ]
    }
    results = parse_tavily(payload)
    assert [r.url for r in results] == ["https://a.com", "https://b.com"]
    assert results[0].snippet == "正文摘要 A"


def test_parse_serper():
    payload = {"organic": [{"title": "T", "link": "https://x.com", "snippet": "S"}]}
    results = parse_serper(payload)
    assert results[0].url == "https://x.com"
    assert results[0].title == "T"


def test_parse_handles_missing_fields():
    assert parse_tavily({}) == []
    assert parse_serper({"organic": [{}]})[0].url == ""


# ------------------------------------------------------------------ 后端选择
def test_provider_uses_configured_backend_when_key_present(settings):
    settings.search_provider = "tavily"
    settings.tavily_api_key = "tvly-xxx"
    assert resolve_provider(settings) == ("tavily", "")


def test_provider_falls_back_loudly_without_key(settings):
    """静默降级会让人以为付费后端已生效，必须给出提示。"""
    settings.search_provider = "tavily"
    settings.tavily_api_key = ""

    provider, notice = resolve_provider(settings)
    assert provider == "duckduckgo"
    assert "TAVILY_API_KEY" in notice
    assert "退回 DuckDuckGo" in notice


def test_provider_defaults_to_duckduckgo(settings):
    settings.search_provider = "duckduckgo"
    assert resolve_provider(settings) == ("duckduckgo", "")

    settings.search_provider = "不存在的后端"
    assert resolve_provider(settings) == ("duckduckgo", "")


PAGE_HTML = """
<html>
<head><title>向量数据库选型指南</title><style>body{color:red}</style></head>
<body>
  <script>var a = 1;</script>
  <h1>选型指南</h1>
  <p>第一段：Milvus 适合大规模场景。</p>
  <p>第二段：Qdrant 更轻量 &amp; 易部署。</p>
  <ul><li>要点一</li><li>要点二</li></ul>
</body>
</html>
"""


def test_html_to_page_extracts_title_and_text():
    page = html_to_page(PAGE_HTML)
    assert page.title == "向量数据库选型指南"
    assert "Milvus 适合大规模场景" in page.text
    assert "更轻量 & 易部署" in page.text
    assert "var a = 1" not in page.text, "脚本内容必须剔除"
    assert "color:red" not in page.text, "样式内容必须剔除"
    assert "<p>" not in page.text


def test_html_to_page_without_title():
    page = html_to_page("<html><body><p>裸正文</p></body></html>")
    assert page.title == ""
    assert page.text == "裸正文"
