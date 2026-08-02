# Content Extraction and arXiv URL Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace regex HTML stripping with a testable trafilatura pipeline, add optional Playwright fallback, and normalize arXiv URLs into structured abstract-page evidence.

**Architecture:** A runtime-owned `PageFetcher` handles HTTP, extraction, arXiv adaptation, and optional browser rendering. `fetch_url` remains the tool boundary and continues to own evidence ingestion and ToolResult formatting. The browser is lazy, optional, serialized by a lock, and closed with `Runtime`.

**Tech Stack:** Python 3.11+, httpx, trafilatura, optional Playwright sync API, pytest, Ruff.

## Global Constraints

- Keep `fetch_url` and EvidenceStore public behavior compatible, including `[S<n>]` labels and duplicate-source hints.
- Do not add a SQLite migration.
- Do not download or parse arXiv PDFs and do not add arXiv search.
- All automated tests are offline; use `httpx.MockTransport` and fake browsers.
- The browser fallback runs only when static extraction is shorter than 80 characters.
- Preserve the user-supplied URL in error text, but ingest the canonical URL returned by `PageFetcher`.
- Use the package manager to select dependency versions; do not hand-edit invented versions.
- Commit steps are executed only if the user explicitly authorizes commits.

## File Map

- Create `src/scout/content/__init__.py`: public content-pipeline exports.
- Create `src/scout/content/models.py`: fetched-page and cancellation-probe types.
- Create `src/scout/content/html_extractor.py`: trafilatura wrapper and HTML title extraction.
- Create `src/scout/content/page_fetcher.py`: HTTP/content-type/static extraction orchestration.
- Create `src/scout/content/arxiv.py`: arXiv URL normalization and citation metadata parser.
- Create `src/scout/content/browser.py`: lazy synchronous Playwright renderer.
- Modify `src/scout/tools/base.py`: inject `page_fetcher` and cancellation into ToolContext.
- Modify `src/scout/tools/web.py`: delegate fetching and retain evidence/preview behavior.
- Modify `src/scout/runtime.py`: own, inject, and close the fetcher.
- Modify `pyproject.toml` and `uv.lock`: trafilatura default dependency and Playwright optional dependency.
- Create `tests/test_html_extractor.py`, `tests/test_page_fetcher.py`, `tests/test_arxiv.py`, `tests/test_fetch_url.py`, `tests/test_browser_renderer.py`.
- Modify `tests/test_search_parse.py`: remove tests tied to deleted `html_to_page`.

---

### Task 1: Introduce the content model and trafilatura extractor

**Files:**
- Create: `src/scout/content/__init__.py`
- Create: `src/scout/content/models.py`
- Create: `src/scout/content/html_extractor.py`
- Create: `tests/test_html_extractor.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces: `FetchedPage(url: str, title: str, text: str)`.
- Produces: `CancellationProbe.is_cancelled() -> bool`.
- Produces: `HtmlExtractor.extract(raw: str, url: str) -> FetchedPage`.

- [ ] **Step 1: Write the failing extractor tests**

```python
# tests/test_html_extractor.py
from scout.content.html_extractor import HtmlExtractor

NOISY_HTML = """
<html>
  <head><title>Example &amp; Test</title><style>.ad{display:none}</style></head>
  <body>
    <nav>Home Pricing Login</nav>
    <article>
      <h1>Useful heading</h1>
      <p>This is the first useful paragraph with enough detail for extraction.</p>
      <p>This is the second useful paragraph and it must remain in the result.</p>
    </article>
    <script>window.secret = "do not keep";</script>
  </body>
</html>
"""


def test_extracts_title_and_main_text_without_navigation():
    page = HtmlExtractor().extract(NOISY_HTML, "https://example.test/article")

    assert page.url == "https://example.test/article"
    assert page.title == "Example & Test"
    assert "first useful paragraph" in page.text
    assert "second useful paragraph" in page.text
    assert "Home Pricing Login" not in page.text
    assert "window.secret" not in page.text


def test_empty_html_returns_empty_text():
    page = HtmlExtractor().extract("<html><body><script>x()</script></body></html>", "https://x.test")
    assert page.text == ""
```

- [ ] **Step 2: Run the tests and confirm the import failure**

Run: `uv run pytest tests/test_html_extractor.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'scout.content'`.

- [ ] **Step 3: Add trafilatura through uv**

Run: `uv add trafilatura`

Expected: `pyproject.toml` and `uv.lock` change, and dependency resolution succeeds.

- [ ] **Step 4: Implement the model and extractor**

```python
# src/scout/content/models.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class FetchedPage:
    url: str
    title: str
    text: str


class CancellationProbe(Protocol):
    def is_cancelled(self) -> bool: ...
```

```python
# src/scout/content/html_extractor.py
from __future__ import annotations

from html.parser import HTMLParser

from trafilatura import extract

from .models import FetchedPage


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.parts.append(data)


class HtmlExtractor:
    def extract(self, raw: str, url: str) -> FetchedPage:
        parser = _TitleParser()
        parser.feed(raw)
        title = " ".join("".join(parser.parts).split())
        text = extract(
            raw,
            url=url,
            output_format="txt",
            include_comments=False,
            include_tables=True,
            favor_precision=True,
        ) or ""
        return FetchedPage(url=url, title=title, text=text.strip())
```

```python
# src/scout/content/__init__.py
from .html_extractor import HtmlExtractor
from .models import CancellationProbe, FetchedPage

__all__ = ["CancellationProbe", "FetchedPage", "HtmlExtractor"]
```

- [ ] **Step 5: Run focused tests and lint**

Run: `uv run pytest tests/test_html_extractor.py -v && uv run ruff check src/scout/content tests/test_html_extractor.py`

Expected: all extractor tests PASS and Ruff exits 0.

- [ ] **Step 6: Commit if explicitly authorized**

```bash
git add pyproject.toml uv.lock src/scout/content tests/test_html_extractor.py
git commit -m "$(cat <<'EOF'
feat: add structured HTML extraction

EOF
)"
```

---

### Task 2: Build the static PageFetcher

**Files:**
- Create: `src/scout/content/page_fetcher.py`
- Create: `tests/test_page_fetcher.py`
- Modify: `src/scout/content/__init__.py`

**Interfaces:**
- Consumes: `HtmlExtractor.extract(raw, url) -> FetchedPage`.
- Produces: `FetchError`.
- Produces: `PageFetcher.fetch(url, cancellation=None) -> FetchedPage`.
- Produces: `PageFetcher.close() -> None`.

- [ ] **Step 1: Write failing HTTP and content-type tests**

```python
# tests/test_page_fetcher.py
import httpx
import pytest

from scout.content import FetchError, PageFetcher


def make_fetcher(body: bytes, content_type: str = "text/html", status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body, headers={"content-type": content_type})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return PageFetcher(client=client)


def test_fetches_and_extracts_static_html():
    html = b"""<html><head><title>Article</title></head><body><article>
    <p>This paragraph is deliberately long enough to pass the minimum content threshold.</p>
    <p>It contains stable evidence for a deterministic offline test.</p>
    </article></body></html>"""
    page = make_fetcher(html).fetch("https://example.test/article")
    assert page.title == "Article"
    assert "deterministic offline test" in page.text


@pytest.mark.parametrize("url", ["file:///tmp/x", "ftp://example.test/x", "javascript:x"])
def test_rejects_non_http_urls(url):
    with pytest.raises(FetchError, match="http"):
        make_fetcher(b"unused").fetch(url)


def test_rejects_pdf_bytes():
    with pytest.raises(FetchError, match="PDF"):
        make_fetcher(b"%PDF-1.7 fake", "application/pdf").fetch("https://example.test/paper.pdf")


def test_surfaces_http_status():
    with pytest.raises(FetchError, match="503"):
        make_fetcher(b"down", status=503).fetch("https://example.test/down")


def test_accepts_plain_text():
    text = ("plain text evidence " * 10).encode()
    page = make_fetcher(text, "text/plain; charset=utf-8").fetch("https://example.test/a.txt")
    assert page.title == "a.txt"
    assert "plain text evidence" in page.text


def test_short_static_page_fails_without_browser():
    with pytest.raises(FetchError, match="正文过短"):
        make_fetcher(b"<html><body>short</body></html>").fetch("https://example.test/app")
```

- [ ] **Step 2: Run tests and confirm `PageFetcher` is missing**

Run: `uv run pytest tests/test_page_fetcher.py -v`

Expected: FAIL because `FetchError` and `PageFetcher` are not exported.

- [ ] **Step 3: Implement the static fetcher**

```python
# src/scout/content/page_fetcher.py
from __future__ import annotations

from pathlib import PurePosixPath

import httpx

from .html_extractor import HtmlExtractor
from .models import CancellationProbe, FetchedPage

USER_AGENT = "Mozilla/5.0 (compatible; scout-agent/0.1; +https://github.com/)"
MIN_TEXT_CHARS = 80


class FetchError(RuntimeError):
    pass


class PageFetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        extractor: HtmlExtractor | None = None,
        browser=None,
    ) -> None:
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        self.extractor = extractor or HtmlExtractor()
        self.browser = browser

    def fetch(
        self,
        url: str,
        cancellation: CancellationProbe | None = None,
    ) -> FetchedPage:
        if not url.startswith(("http://", "https://")):
            raise FetchError("URL 必须以 http:// 或 https:// 开头")
        if cancellation and cancellation.is_cancelled():
            raise FetchError("运行已取消")

        try:
            response = self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise FetchError(f"抓取失败：HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise FetchError(f"抓取失败：{exc}") from exc

        content_type = response.headers.get("content-type", "").lower()
        if "application/pdf" in content_type or response.content.startswith(b"%PDF"):
            raise FetchError("暂不支持 PDF 正文，请提供 HTML 页面")
        if "html" in content_type:
            page = self.extractor.extract(response.text, str(response.url))
        elif content_type.startswith("text/"):
            name = PurePosixPath(response.url.path).name or str(response.url)
            page = FetchedPage(str(response.url), name, response.text.strip())
        else:
            raise FetchError(f"不支持的 Content-Type：{content_type or 'unknown'}")

        if len(page.text) < MIN_TEXT_CHARS:
            raise FetchError(f"正文过短（{len(page.text)} 字符），可能是动态渲染页或反爬拦截")
        return page

    def close(self) -> None:
        if self.browser is not None:
            self.browser.close()
        if self._owns_client:
            self.client.close()
```

Add `FetchError` and `PageFetcher` to `src/scout/content/__init__.py`.

- [ ] **Step 4: Run the static fetcher tests**

Run: `uv run pytest tests/test_page_fetcher.py -v`

Expected: all static fetcher tests PASS.

- [ ] **Step 5: Commit if explicitly authorized**

```bash
git add src/scout/content tests/test_page_fetcher.py
git commit -m "$(cat <<'EOF'
feat: add static page fetching pipeline

EOF
)"
```

---

### Task 3: Route `fetch_url` through the runtime-owned fetcher

**Files:**
- Create: `tests/test_fetch_url.py`
- Modify: `src/scout/tools/base.py`
- Modify: `src/scout/tools/web.py`
- Modify: `src/scout/runtime.py`
- Modify: `tests/test_search_parse.py`

**Interfaces:**
- Consumes: `PageFetcher.fetch(url, cancellation) -> FetchedPage`.
- Produces: `ToolContext.page_fetcher` and `ToolContext.cancellation`.
- Preserves: `fetch_url(ctx, url, preview_chars=6000) -> ToolResult`.

- [ ] **Step 1: Write failing tool delegation tests**

```python
# tests/test_fetch_url.py
from scout.content import FetchError, FetchedPage
from scout.memory.evidence import EvidenceStore
from scout.tools.base import ToolContext
from scout.tools.web import fetch_url


class FakeFetcher:
    def __init__(self, page=None, error=None):
        self.page = page
        self.error = error
        self.urls = []

    def fetch(self, url, cancellation=None):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.page


def test_fetch_url_ingests_fetcher_result(workspace, store, fake_llm):
    fetcher = FakeFetcher(
        FetchedPage("https://canonical.test/a", "Canonical title", "evidence " * 30)
    )
    evidence = EvidenceStore(store, "session-a", fake_llm)
    ctx = ToolContext(workspace=workspace, evidence=evidence, page_fetcher=fetcher)

    result = fetch_url.run(ctx, url="https://input.test/a")

    assert result.ok is True
    assert "[S1]" in result.content
    assert evidence.sources()[0]["url"] == "https://canonical.test/a"


def test_fetch_url_failure_does_not_ingest(workspace, store, fake_llm):
    fetcher = FakeFetcher(error=FetchError("正文过短"))
    evidence = EvidenceStore(store, "session-b", fake_llm)
    ctx = ToolContext(workspace=workspace, evidence=evidence, page_fetcher=fetcher)

    result = fetch_url.run(ctx, url="https://input.test/a")

    assert result.ok is False
    assert "正文过短" in result.content
    assert evidence.count() == 0
```

- [ ] **Step 2: Run tests and verify ToolContext rejects `page_fetcher`**

Run: `uv run pytest tests/test_fetch_url.py -v`

Expected: FAIL with an unexpected `page_fetcher` argument or missing delegation.

- [ ] **Step 3: Add injected fetcher fields and simplify the tool**

Add to `ToolContext`:

```python
page_fetcher: Any = None
cancellation: Any = None
run_id: str = ""
```

Replace the network/extraction portion of `fetch_url` with:

```python
if ctx.page_fetcher is None:
    return ToolResult.failure("网页抓取器未初始化")
try:
    page = ctx.page_fetcher.fetch(url, cancellation=ctx.cancellation)
except FetchError as exc:
    return ToolResult.failure(f"{url} 抓取失败：{exc}。可以换一个来源")

canonical_url = page.url
label = ""
chunks = 0
if ctx.evidence is not None:
    label, chunks, is_new = ctx.evidence.ingest(canonical_url, page.title or canonical_url, page.text)
```

Retain the existing duplicate hint, preview truncation, display, `label`, and `chars` metadata. Remove `Page`, regex constants, `html_to_page`, and the direct `httpx.get` call from `src/scout/tools/web.py`; keep `http_request` unchanged.

- [ ] **Step 4: Own and inject the fetcher from Runtime**

```python
# additions in src/scout/runtime.py
from .content import PageFetcher

# Runtime.__init__
self.page_fetcher = PageFetcher()

# Runtime.build_agent
ctx = ToolContext(
    workspace=self.settings.workspace,
    settings=self.settings,
    llm=self.llm,
    memory=self.memory,
    evidence=session.evidence,
    session=session,
    cancellation=cancellation,
    page_fetcher=self.page_fetcher,
    emit=lambda name, data: self.bus.emit(name, data),
)

# Runtime.close, before store.close()
self.page_fetcher.close()
self.store.close()
```

- [ ] **Step 5: Remove obsolete parser tests and run focused regression**

Delete only `html_to_page` imports/tests from `tests/test_search_parse.py`; preserve search-provider parsing tests.

Run: `uv run pytest tests/test_fetch_url.py tests/test_search_parse.py tests/test_agent_loop.py -v`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit if explicitly authorized**

```bash
git add src/scout/tools/base.py src/scout/tools/web.py src/scout/runtime.py tests/test_fetch_url.py tests/test_search_parse.py
git commit -m "$(cat <<'EOF'
refactor: route web evidence through page fetcher

EOF
)"
```

---

### Task 4: Add arXiv URL normalization and abstract metadata

**Files:**
- Create: `src/scout/content/arxiv.py`
- Create: `tests/test_arxiv.py`
- Modify: `src/scout/content/page_fetcher.py`
- Modify: `src/scout/content/__init__.py`
- Modify: `tests/test_page_fetcher.py`
- Modify: `tests/test_fetch_url.py`

**Interfaces:**
- Produces: `normalize_arxiv_url(url: str) -> str | None`.
- Produces: `parse_arxiv_page(raw: str, canonical_url: str) -> FetchedPage | None`.
- PageFetcher returns canonical `https://arxiv.org/abs/<id>` URLs.

- [ ] **Step 1: Write table-driven URL and metadata tests**

```python
# tests/test_arxiv.py
import pytest

from scout.content.arxiv import normalize_arxiv_url, parse_arxiv_page


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://arxiv.org/abs/2401.12345", "https://arxiv.org/abs/2401.12345"),
        ("https://arxiv.org/pdf/2401.12345.pdf", "https://arxiv.org/abs/2401.12345"),
        ("http://www.arxiv.org/pdf/2401.12345v2", "https://arxiv.org/abs/2401.12345v2"),
        ("https://arxiv.org/abs/cs/9901001", "https://arxiv.org/abs/cs/9901001"),
    ],
)
def test_normalizes_arxiv_urls(source, expected):
    assert normalize_arxiv_url(source) == expected


def test_non_arxiv_url_returns_none():
    assert normalize_arxiv_url("https://example.test/abs/2401.12345") is None


def test_parses_structured_citation_metadata():
    raw = """
    <html><head>
      <meta name="citation_title" content="A Reliable Agent">
      <meta name="citation_author" content="Ada Example">
      <meta name="citation_author" content="Lin Researcher">
      <meta name="citation_date" content="2026/08/01">
      <meta name="citation_keywords" content="cs.AI; cs.CL">
      <meta name="citation_abstract" content="This abstract is intentionally long enough to become reliable evidence in Scout's evidence store.">
    </head></html>
    """
    page = parse_arxiv_page(raw, "https://arxiv.org/abs/2401.12345")
    assert page is not None
    assert page.title == "A Reliable Agent"
    assert "Ada Example, Lin Researcher" in page.text
    assert "cs.AI; cs.CL" in page.text
    assert "intentionally long enough" in page.text
```

- [ ] **Step 2: Run tests and verify the module is absent**

Run: `uv run pytest tests/test_arxiv.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement normalization and metadata parsing**

```python
# src/scout/content/arxiv.py
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .models import FetchedPage

_MODERN = re.compile(r"^\d{4}\.\d{4,5}(?:v\d+)?$", re.I)
_LEGACY = re.compile(r"^[a-z-]+(?:\.[a-z]{2})?/\d{7}(?:v\d+)?$", re.I)


def normalize_arxiv_url(url: str) -> str | None:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if host not in {"arxiv.org", "www.arxiv.org"}:
        return None
    path = parsed.path.strip("/")
    if path.startswith("abs/"):
        paper_id = path[4:]
    elif path.startswith("pdf/"):
        paper_id = path[4:]
    else:
        return None
    if paper_id.endswith(".pdf"):
        paper_id = paper_id[:-4]
    if not (_MODERN.fullmatch(paper_id) or _LEGACY.fullmatch(paper_id)):
        return None
    return f"https://arxiv.org/abs/{paper_id}"


class _CitationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() != "meta":
            return
        data = {str(key).lower(): value for key, value in attrs}
        name = str(data.get("name", "")).lower()
        content = str(data.get("content", "")).strip()
        if name.startswith("citation_") and content:
            self.values.setdefault(name, []).append(content)


def parse_arxiv_page(raw: str, canonical_url: str) -> FetchedPage | None:
    parser = _CitationParser()
    parser.feed(raw)
    title = _first(parser.values, "citation_title")
    abstract = _first(parser.values, "citation_abstract")
    if not title or not abstract:
        return None
    authors = ", ".join(parser.values.get("citation_author", []))
    date = _first(parser.values, "citation_date")
    categories = _first(parser.values, "citation_keywords")
    text = "\n\n".join(
        part
        for part in [
            f"Title: {title}",
            f"Authors: {authors}" if authors else "",
            f"Submitted: {date}" if date else "",
            f"Categories: {categories}" if categories else "",
            f"Abstract:\n{abstract}",
            f"URL: {canonical_url}",
        ]
        if part
    )
    return FetchedPage(canonical_url, title, text)


def _first(values: dict[str, list[str]], key: str) -> str:
    items = values.get(key, [])
    return items[0] if items else ""
```

- [ ] **Step 4: Integrate arXiv before generic extraction**

In `PageFetcher.fetch`:

```python
arxiv_url = normalize_arxiv_url(url)
canonical_url = arxiv_url or url
try:
    response = self.client.get(canonical_url)
    response.raise_for_status()
except httpx.HTTPStatusError as exc:
    raise FetchError(f"抓取失败：HTTP {exc.response.status_code}") from exc
except httpx.HTTPError as exc:
    raise FetchError(f"抓取失败：{exc}") from exc

content_type = response.headers.get("content-type", "").lower()
if "application/pdf" in content_type or response.content.startswith(b"%PDF"):
    raise FetchError("暂不支持 PDF 正文，请提供 HTML 页面")
if arxiv_url and "html" in content_type:
    page = parse_arxiv_page(response.text, canonical_url)
    if page is None:
        page = self.extractor.extract(response.text, canonical_url)
elif "html" in content_type:
    page = self.extractor.extract(response.text, str(response.url))
elif content_type.startswith("text/"):
    name = PurePosixPath(response.url.path).name or str(response.url)
    page = FetchedPage(str(response.url), name, response.text.strip())
else:
    raise FetchError(f"不支持的 Content-Type：{content_type or 'unknown'}")
```

Add a MockTransport test asserting a PDF-form input requests `/abs/2401.12345`, and extend `test_fetch_url.py` to assert both PDF and abs input deduplicate to one canonical source.

- [ ] **Step 5: Run arXiv and tool tests**

Run: `uv run pytest tests/test_arxiv.py tests/test_page_fetcher.py tests/test_fetch_url.py -v`

Expected: all selected tests PASS.

- [ ] **Step 6: Commit if explicitly authorized**

```bash
git add src/scout/content tests/test_arxiv.py tests/test_page_fetcher.py tests/test_fetch_url.py
git commit -m "$(cat <<'EOF'
feat: ingest arXiv abstract pages

EOF
)"
```

---

### Task 5: Add lazy Playwright rendering fallback

**Files:**
- Create: `src/scout/content/browser.py`
- Create: `tests/test_browser_renderer.py`
- Modify: `src/scout/content/page_fetcher.py`
- Modify: `src/scout/content/__init__.py`
- Modify: `src/scout/runtime.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `tests/test_page_fetcher.py`

**Interfaces:**
- Produces: `BrowserRenderer.render(url: str) -> str`.
- Produces: idempotent `BrowserRenderer.close()`.
- PageFetcher invokes the browser only after short static extraction and before final failure.

- [ ] **Step 1: Write the failing fallback tests with a fake browser**

```python
# append to tests/test_page_fetcher.py
class FakeBrowser:
    def __init__(self, rendered_html):
        self.rendered_html = rendered_html
        self.urls = []
        self.closed = False

    def render(self, url):
        self.urls.append(url)
        return self.rendered_html

    def close(self):
        self.closed = True


def test_short_static_page_uses_browser_then_reextracts():
    rendered = """<html><head><title>Rendered</title></head><body><article>
    <p>The browser-rendered article contains enough meaningful evidence to pass extraction.</p>
    <p>A second paragraph ensures the result is not considered an empty application shell.</p>
    </article></body></html>"""
    browser = FakeBrowser(rendered)
    fetcher = make_fetcher(b"<html><body><div id='app'></div></body></html>")
    fetcher.browser = browser

    page = fetcher.fetch("https://example.test/app")

    assert page.title == "Rendered"
    assert browser.urls == ["https://example.test/app"]


def test_browser_is_not_used_for_good_static_html():
    browser = FakeBrowser("<html></html>")
    fetcher = make_fetcher(("<article>" + "static evidence " * 20 + "</article>").encode())
    fetcher.browser = browser
    fetcher.fetch("https://example.test/static")
    assert browser.urls == []
```

- [ ] **Step 2: Run the fallback tests and confirm they fail**

Run: `uv run pytest tests/test_page_fetcher.py -k browser -v`

Expected: FAIL because PageFetcher raises before calling the browser.

- [ ] **Step 3: Add Playwright as an optional dependency**

Run: `uv add --optional browser playwright`

Expected: the `browser` optional dependency group and `uv.lock` are updated.

- [ ] **Step 4: Implement the lazy locked renderer**

```python
# src/scout/content/browser.py
from __future__ import annotations

import threading
from collections.abc import Callable

from .page_fetcher import FetchError


class BrowserRenderer:
    def __init__(self, timeout_ms: int = 30000, starter: Callable | None = None) -> None:
        self.timeout_ms = timeout_ms
        self._starter = starter
        self._lock = threading.RLock()
        self._playwright = None
        self._browser = None

    def render(self, url: str) -> str:
        with self._lock:
            self._ensure_started()
            page = self._browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
                return page.content()
            except Exception as exc:
                raise FetchError(f"浏览器渲染失败：{exc}") from exc
            finally:
                page.close()

    def _ensure_started(self) -> None:
        if self._browser is not None:
            return
        try:
            if self._starter is None:
                from playwright.sync_api import sync_playwright

                self._starter = sync_playwright
            self._playwright = self._starter().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:
            raise FetchError(
                "Playwright Chromium 不可用；请安装 browser extra 后运行 "
                "`uv run playwright install chromium`"
            ) from exc

    def close(self) -> None:
        with self._lock:
            if self._browser is not None:
                self._browser.close()
                self._browser = None
            if self._playwright is not None:
                self._playwright.stop()
                self._playwright = None
```

- [ ] **Step 5: Invoke the browser only on short content**

Refactor PageFetcher HTML handling to:

```python
page = self._extract_html(response.text, canonical_url)
if len(page.text) < MIN_TEXT_CHARS and self.browser is not None:
    if cancellation and cancellation.is_cancelled():
        raise FetchError("运行已取消")
    rendered = self.browser.render(canonical_url)
    page = self._extract_html(rendered, canonical_url)
if len(page.text) < MIN_TEXT_CHARS:
    raise FetchError(f"正文过短（{len(page.text)} 字符），可能是动态渲染页或反爬拦截")
```

Construct `PageFetcher(browser=BrowserRenderer())` in Runtime. Because import is lazy, normal static extraction does not require the optional package.

- [ ] **Step 6: Test close behavior and full regression**

Add `tests/test_browser_renderer.py` with a fake starter that records `browser.close()` and `playwright.stop()`, then call `close()` twice and assert both underlying methods run once.

Run: `uv run pytest tests/test_html_extractor.py tests/test_page_fetcher.py tests/test_arxiv.py tests/test_fetch_url.py tests/test_browser_renderer.py -v && uv run pytest tests -q && uv run ruff check src tests`

Expected: focused tests PASS, full suite PASS, Ruff exits 0.

- [ ] **Step 7: Commit if explicitly authorized**

```bash
git add pyproject.toml uv.lock src/scout/content src/scout/runtime.py tests
git commit -m "$(cat <<'EOF'
feat: add browser fallback for dynamic pages

EOF
)"
```

## Plan Completion Check

- Run `uv run pytest tests -q`.
- Run `uv run ruff check src tests`.
- Manually, only when browser support is installed: `uv sync --extra browser && uv run playwright install chromium`.
- Confirm a normal static fixture never starts Chromium.
- Confirm arXiv PDF-form and abs-form URLs create one source.
- Confirm PDF responses do not enter EvidenceStore.
