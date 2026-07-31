"""不调用大模型的冒烟脚本：验证搜索、抓取、证据入库、检索这条链路。

    uv run python examples/smoke_tools.py
"""

from __future__ import annotations

from pathlib import Path

from scout.config import load_settings
from scout.llm.base import ToolCall
from scout.memory.evidence import EvidenceStore
from scout.memory.store import Store
from scout.tools import build_registry
from scout.tools.base import ToolContext


def main() -> None:
    settings = load_settings(Path.cwd())
    store = Store(settings.db_path)
    session_id = store.create_session("smoke")
    evidence = EvidenceStore(store, session_id)

    ctx = ToolContext(workspace=settings.workspace, settings=settings, evidence=evidence)
    registry = build_registry(ctx)

    print("1) 搜索")
    result = registry.execute(
        ToolCall(id="1", name="web_search", arguments={"query": "Milvus 向量数据库", "limit": 3})
    )
    print(f"   ok={result.ok} {result.display}")
    print("   " + "\n   ".join(result.content.splitlines()[:8]))

    first_url = next(
        (line.strip() for line in result.content.splitlines() if line.strip().startswith("http")),
        "https://milvus.io/docs/overview.md",
    )

    print(f"\n2) 抓取 {first_url}")
    fetched = registry.execute(ToolCall(id="2", name="fetch_url", arguments={"url": first_url}))
    print(f"   ok={fetched.ok} {fetched.display}")

    print("\n3) 证据检索")
    hits = registry.execute(
        ToolCall(id="3", name="search_evidence", arguments={"query": "向量检索"})
    )
    print(f"   ok={hits.ok} {hits.display}")

    print("\n4) 来源清单")
    print(registry.execute(ToolCall(id="4", name="list_sources")).content)

    store.close()


if __name__ == "__main__":
    main()
