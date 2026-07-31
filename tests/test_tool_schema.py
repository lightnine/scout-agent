"""工具层：Schema 自动生成与参数校验。"""

from __future__ import annotations

from typing import Annotated, Literal

import pytest

from scout.tools.base import Risk, ToolContext, ToolResult, tool


@tool(risk=Risk.CAUTION)
def sample(
    ctx: ToolContext,
    query: Annotated[str, "搜索词"],
    limit: Annotated[int, "条数"] = 5,
    mode: Annotated[Literal["fast", "deep"], "模式"] = "fast",
    tags: Annotated[list[str], "标签"] = [],
    ratio: float = 0.5,
    flag: bool = False,
) -> str:
    """示例工具。"""
    return f"{query}-{limit}-{mode}-{tags}-{ratio}-{flag}"


def test_schema_generated_from_signature():
    schema = sample.to_openai_schema()["function"]
    assert schema["name"] == "sample"
    assert schema["description"] == "示例工具。"

    props = schema["parameters"]["properties"]
    assert "ctx" not in props, "ctx 是运行时注入的，不应暴露给模型"
    assert props["query"] == {"type": "string", "description": "搜索词"}
    assert props["limit"]["type"] == "integer"
    assert props["mode"]["enum"] == ["fast", "deep"]
    assert props["tags"] == {"type": "array", "items": {"type": "string"}, "description": "标签", "default": []}
    assert props["ratio"]["type"] == "number"
    assert props["flag"]["type"] == "boolean"
    assert schema["parameters"]["required"] == ["query"]


def test_missing_required_argument_returns_error_not_exception():
    result = sample.run(None, limit=3)
    assert result.ok is False
    assert "缺少必填参数" in result.content


def test_unknown_argument_is_rejected():
    result = sample.run(None, query="x", nonexistent=1)
    assert result.ok is False
    assert "不存在的参数" in result.content


def test_tool_exception_becomes_error_result():
    @tool()
    def boom() -> str:
        """会抛异常的工具。"""
        raise ValueError("炸了")

    result = boom.run()
    assert result.ok is False
    assert "ValueError: 炸了" in result.content


def test_risk_defaults_control_concurrency():
    assert sample.concurrency_safe is False, "有副作用的工具默认不并发"

    @tool(risk=Risk.SAFE)
    def readonly() -> str:
        """只读工具。"""
        return "ok"

    assert readonly.concurrency_safe is True


def test_context_resolve_blocks_path_escape(tmp_path):
    ctx = ToolContext(workspace=tmp_path)
    assert ctx.resolve("a/b.txt") == (tmp_path / "a/b.txt").resolve()
    with pytest.raises(PermissionError):
        ctx.resolve("../../etc/passwd")


def test_tool_result_helpers():
    ok = ToolResult.success("第一行\n第二行")
    assert ok.ok and ok.display == "第一行"
    bad = ToolResult.failure("出错了")
    assert bad.ok is False and "出错了" in bad.content
