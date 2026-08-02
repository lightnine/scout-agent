"""注册表：鉴权、批量执行、结果截断。"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from typing import Annotated

from scout.approval import ApprovalAction, ApprovalDecision
from scout.llm.base import ToolCall
from scout.permissions import PolicyApprover
from scout.tools.base import Risk, ToolContext, tool
from scout.tools.registry import MAX_RESULT_CHARS, ToolRegistry


class FixedGateway:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def request(self, request, emit=None):
        self.requests.append(request)
        return self.decision


@tool(risk=Risk.SAFE)
def slow_echo(text: Annotated[str, "内容"]) -> str:
    """睡 0.1 秒后回显，用来观察是否并行。"""
    time.sleep(0.1)
    return f"{text}@{threading.get_ident()}"


@tool(risk=Risk.CAUTION)
def mutate(value: Annotated[str, "内容"]) -> str:
    """有副作用的工具。"""
    return f"changed {value}"


@tool(risk=Risk.SAFE)
def huge() -> str:
    """返回超长内容。"""
    return "x" * (MAX_RESULT_CHARS + 5000)


def make_registry(tmp_path, approver=None) -> ToolRegistry:
    registry = ToolRegistry(ToolContext(workspace=tmp_path), approver=approver)
    registry.register_all([slow_echo, mutate, huge])
    return registry


def test_unknown_tool_returns_helpful_error(tmp_path):
    result = make_registry(tmp_path).execute(ToolCall(id="1", name="nope"))
    assert result.ok is False
    assert "不存在名为 nope 的工具" in result.content
    assert "slow_echo" in result.content


def test_readonly_mode_blocks_side_effects(tmp_path):
    registry = make_registry(tmp_path, PolicyApprover("readonly"))
    assert registry.execute(ToolCall(id="1", name="mutate", arguments={"value": "a"})).ok is False
    assert registry.execute(ToolCall(id="2", name="slow_echo", arguments={"text": "a"})).ok is True


def test_user_rejection_is_surfaced_to_model(tmp_path):
    gateway = FixedGateway(ApprovalDecision(ApprovalAction.REJECT, "not now"))
    approver = PolicyApprover("ask", gateway=gateway)
    ctx = ToolContext(workspace=tmp_path, session=SimpleNamespace(id="s1"))
    ctx.run_id = "r1"
    registry = ToolRegistry(ctx, approver=approver)
    registry.register_all([slow_echo, mutate, huge])
    result = registry.execute(ToolCall(id="1", name="mutate", arguments={"value": "a"}))
    assert result.ok is False
    assert "not now" in result.content


def test_readonly_calls_run_in_parallel(tmp_path):
    registry = make_registry(tmp_path)
    calls = [ToolCall(id=str(i), name="slow_echo", arguments={"text": str(i)}) for i in range(4)]
    started = time.monotonic()
    results = registry.execute_batch(calls)
    elapsed = time.monotonic() - started

    assert all(r.ok for r in results)
    assert elapsed < 0.35, "4 个只读工具应当并行执行"


def test_batch_falls_back_to_serial_when_side_effects_present(tmp_path):
    registry = make_registry(tmp_path)
    calls = [
        ToolCall(id="1", name="slow_echo", arguments={"text": "a"}),
        ToolCall(id="2", name="mutate", arguments={"value": "b"}),
    ]
    results = registry.execute_batch(calls)
    assert [r.ok for r in results] == [True, True]


def test_oversized_output_is_truncated(tmp_path):
    result = make_registry(tmp_path).execute(ToolCall(id="1", name="huge"))
    assert result.meta["truncated"] is True
    assert len(result.content) < MAX_RESULT_CHARS + 200


def test_subset_limits_available_tools(tmp_path):
    child = make_registry(tmp_path).subset(["slow_echo"])
    assert [t.name for t in child.tools] == ["slow_echo"]
    assert child.execute(ToolCall(id="1", name="mutate", arguments={"value": "x"})).ok is False


def test_cached_schemas_sorted_and_memoized(tmp_path):
    registry = make_registry(tmp_path)
    first = registry.cached_schemas()
    second = registry.cached_schemas()
    assert first is second
    assert [s["function"]["name"] for s in first] == ["huge", "mutate", "slow_echo"]
