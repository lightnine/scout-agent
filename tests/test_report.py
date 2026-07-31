"""报告产出：标题去重、参考文献追加、引用覆盖提示。"""

from __future__ import annotations

from scout.memory.evidence import EvidenceStore
from scout.tools.base import ToolContext
from scout.tools.report import write_report


def make_ctx(workspace, store, with_evidence: bool = True) -> ToolContext:
    evidence = None
    if with_evidence:
        session_id = store.create_session("t")
        evidence = EvidenceStore(store, session_id)
        evidence.ingest("https://a.com", "来源 A", "内容 A" * 50)
        evidence.ingest("https://b.com", "来源 B", "内容 B" * 50)
    return ToolContext(workspace=workspace, evidence=evidence)


def read_report(workspace) -> str:
    files = list((workspace / "reports").glob("*.md"))
    assert len(files) == 1
    return files[0].read_text(encoding="utf-8")


def test_report_appends_bibliography(workspace, store):
    ctx = make_ctx(workspace, store)
    result = write_report.run(ctx, title="调研报告", content="结论一 [S1]，结论二 [S2]。")

    assert result.ok
    document = read_report(workspace)
    assert document.startswith("# 调研报告")
    assert "## 参考来源" in document
    assert "https://a.com" in document and "https://b.com" in document
    assert "引用覆盖：正文引用了 2/2 个来源" in result.content


def test_report_does_not_duplicate_heading(workspace, store):
    ctx = make_ctx(workspace, store)
    write_report.run(ctx, title="调研报告", content="# 我自己写的标题\n\n正文 [S1]")

    document = read_report(workspace)
    assert document.count("# 我自己写的标题") == 1
    assert "# 调研报告" not in document


def test_report_warns_when_no_citations(workspace, store):
    ctx = make_ctx(workspace, store)
    result = write_report.run(ctx, title="没有引用的报告", content="我觉得应该选 A。")
    assert "没有任何 \\[S*] 引用标记" in result.content or "没有任何 [S*] 引用标记" in result.content


def test_report_rejects_empty_content(workspace, store):
    result = write_report.run(make_ctx(workspace, store), title="空报告", content="   ")
    assert result.ok is False


def test_report_works_without_evidence_store(workspace, store):
    ctx = make_ctx(workspace, store, with_evidence=False)
    assert write_report.run(ctx, title="纯推理报告", content="正文").ok
    assert "## 参考来源" not in read_report(workspace)
