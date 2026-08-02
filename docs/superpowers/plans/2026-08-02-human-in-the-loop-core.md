# Human-in-the-Loop Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add UI-independent plan confirmation, risk-tool approval, session-scoped grants, and cooperative run cancellation while preserving CLI and programmatic behavior.

**Architecture:** Approval requests and decisions are core dataclasses consumed through an `ApprovalGateway` protocol. The Agent gates the first non-empty plan before any sibling tool executes; `PolicyApprover` gates risky tools. CLI is one gateway implementation, and the later Web plan supplies another.

**Tech Stack:** Python 3.11+, dataclasses, StrEnum, threading.Event, Rich, pytest, Ruff.

## Global Constraints

- Execute this plan after `2026-08-02-content-extraction-arxiv.md`, so ToolContext already has `page_fetcher`, `cancellation`, and `run_id` fields.
- Only the first non-empty plan in a lead Agent run requires confirmation; progress updates do not.
- Before initial plan approval, every non-`update_plan` call in the same batch is deferred without `tool_start`.
- Worker Agents never request plan confirmation.
- `allow_session` is keyed by `(session_id, tool_name)` and is not restored after resume.
- `auto` skips plan and tool prompts; `readonly` still confirms plans but rejects non-SAFE tools.
- Cancellation is cooperative and does not kill an in-flight external request.
- Existing `scout` arguments and `AgentResult` fields remain compatible; `stop_reason` gains `cancelled`.
- Commit steps are executed only if the user explicitly authorizes commits.

## File Map

- Create `src/scout/approval.py`: approval enums, request/decision data, protocol, event wrapper.
- Create `src/scout/cancellation.py`: cancellation flag and `RunCancelled`.
- Create `src/scout/approval_cli.py`: Rich CLI gateway.
- Modify `src/scout/core/events.py`: approval event types.
- Modify `src/scout/permissions.py`: gateway-backed policy and session-scoped allowlist.
- Modify `src/scout/tools/registry.py`: pass session/run identity to policy.
- Modify `src/scout/core/agent.py`: run id injection, plan gate, deferred tool messages, cancellation boundaries.
- Modify `src/scout/runtime.py`: construct and inject approval/cancellation dependencies.
- Modify `src/scout/cli.py`: use `CliApprovalGateway`.
- Modify `src/scout/core/prompts.py`: tell the model to submit the first plan alone.
- Modify `tests/conftest.py`, `tests/test_registry.py`, and `tests/test_agent_loop.py`.
- Create `tests/test_approval.py`, `tests/test_plan_gate.py`, and `tests/test_cancellation.py`.

---

### Task 1: Define approval and cancellation primitives

**Files:**
- Create: `src/scout/approval.py`
- Create: `src/scout/cancellation.py`
- Create: `tests/test_approval.py`
- Create: `tests/test_cancellation.py`
- Modify: `src/scout/core/events.py`

**Interfaces:**
- Produces: `ApprovalKind`, `ApprovalAction`, `ApprovalRequest`, `ApprovalDecision`.
- Produces: `ApprovalGateway.request(request, emit=None) -> ApprovalDecision`.
- Produces: `RunCancellation.request()`, `clear()`, `is_cancelled()`, `ensure_active()`.

- [ ] **Step 1: Write failing primitive tests**

```python
# tests/test_approval.py
from scout.approval import (
    ApprovalKind,
    ApprovalRequest,
)


def test_request_factory_sets_identity_and_payload():
    request = ApprovalRequest.create(
        run_id="run-1",
        session_id="session-1",
        kind=ApprovalKind.PLAN,
        title="确认计划",
        payload={"plan": "1. research"},
    )
    assert request.id
    assert request.run_id == "run-1"
    assert request.session_id == "session-1"
    assert request.created_at > 0
```

```python
# tests/test_cancellation.py
import pytest

from scout.cancellation import RunCancellation, RunCancelled


def test_cancellation_can_be_requested_and_cleared():
    token = RunCancellation()
    assert token.is_cancelled() is False
    token.request()
    assert token.is_cancelled() is True
    with pytest.raises(RunCancelled):
        token.ensure_active()
    token.clear()
    token.ensure_active()
```

- [ ] **Step 2: Run tests and verify imports fail**

Run: `uv run pytest tests/test_approval.py tests/test_cancellation.py -v`

Expected: FAIL because the modules do not exist.

- [ ] **Step 3: Implement the approval model and event wrapper**

```python
# src/scout/approval.py
from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ApprovalKind(StrEnum):
    PLAN = "plan"
    TOOL = "tool"


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"
    ALLOW_SESSION = "allow_session"
    CANCEL = "cancel"


@dataclass(slots=True)
class ApprovalRequest:
    id: str
    run_id: str
    session_id: str
    kind: ApprovalKind
    title: str
    payload: dict[str, Any]
    created_at: float

    @classmethod
    def create(
        cls,
        run_id: str,
        session_id: str,
        kind: ApprovalKind,
        title: str,
        payload: dict[str, Any],
    ) -> ApprovalRequest:
        return cls(uuid.uuid4().hex, run_id, session_id, kind, title, payload, time.time())

    def event_data(self) -> dict[str, Any]:
        return {
            "approval_id": self.id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "kind": self.kind.value,
            "title": self.title,
            "payload": self.payload,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class ApprovalDecision:
    action: ApprovalAction
    feedback: str = ""


Emitter = Callable[[str, dict[str, Any]], None]


class ApprovalGateway(Protocol):
    def request(
        self,
        request: ApprovalRequest,
        emit: Emitter | None = None,
    ) -> ApprovalDecision: ...


```

Official gateways must register any pending state first, then emit `approval_required`, and emit `approval_resolved` immediately after a decision is fixed. This ordering prevents the Web client from resolving an approval before it exists in the pending map.

- [ ] **Step 4: Implement cooperative cancellation and event types**

```python
# src/scout/cancellation.py
from __future__ import annotations

import threading


class RunCancelled(RuntimeError):
    pass


class RunCancellation:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def ensure_active(self) -> None:
        if self.is_cancelled():
            raise RunCancelled("运行已取消")
```

Add to `EventType`:

```python
APPROVAL_REQUIRED = "approval_required"
APPROVAL_RESOLVED = "approval_resolved"
```

- [ ] **Step 5: Run focused tests and lint**

Run: `uv run pytest tests/test_approval.py tests/test_cancellation.py -v && uv run ruff check src/scout/approval.py src/scout/cancellation.py src/scout/core/events.py`

Expected: all tests PASS and Ruff exits 0.

- [ ] **Step 6: Commit if explicitly authorized**

```bash
git add src/scout/approval.py src/scout/cancellation.py src/scout/core/events.py tests/test_approval.py tests/test_cancellation.py
git commit -m "$(cat <<'EOF'
feat: add approval and cancellation primitives

EOF
)"
```

---

### Task 2: Route risky tools through ApprovalGateway

**Files:**
- Modify: `src/scout/permissions.py`
- Modify: `src/scout/tools/registry.py`
- Modify: `tests/test_approval.py`
- Modify: `tests/test_registry.py`

**Interfaces:**
- Consumes: approval model from Task 1.
- Produces: `PolicyApprover.check(tool, args, *, session_id, run_id) -> Decision`.
- Produces: `PolicyApprover.clear_session(session_id)`.
- ToolRegistry reads `ctx.session.id` and `ctx.run_id`.

- [ ] **Step 1: Add failing policy tests**

```python
# append to tests/test_approval.py
from scout.permissions import PolicyApprover
from scout.tools.base import Risk, tool


class FixedGateway:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def request(self, request, emit=None):
        self.requests.append(request)
        if emit:
            emit("approval_required", request.event_data())
            emit(
                "approval_resolved",
                {
                    "approval_id": request.id,
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "action": self.decision.action.value,
                },
            )
        return self.decision


@tool(risk=Risk.CAUTION)
def risky(value: str) -> str:
    """A test-only risky tool."""
    return value


def test_safe_policy_paths_and_session_scoped_allow():
    gateway = FixedGateway(ApprovalDecision(ApprovalAction.ALLOW_SESSION))
    approver = PolicyApprover("ask", gateway=gateway)

    first = approver.check(risky, {"value": "a"}, session_id="s1", run_id="r1")
    second = approver.check(risky, {"value": "b"}, session_id="s1", run_id="r2")
    other = approver.check(risky, {"value": "c"}, session_id="s2", run_id="r3")

    assert first.allowed and second.allowed and other.allowed
    assert len(gateway.requests) == 2
    assert gateway.requests[0].kind is ApprovalKind.TOOL


def test_reject_is_returned_to_model():
    gateway = FixedGateway(ApprovalDecision(ApprovalAction.REJECT, "not now"))
    decision = PolicyApprover("ask", gateway=gateway).check(
        risky, {"value": "a"}, session_id="s1", run_id="r1"
    )
    assert decision.allowed is False
    assert "not now" in decision.reason
```

Update `tests/test_registry.py::test_user_rejection_is_surfaced_to_model` to use a fixed gateway and a `ToolContext` whose `session.id` is present.

- [ ] **Step 2: Run policy and registry tests**

Run: `uv run pytest tests/test_approval.py tests/test_registry.py -v`

Expected: FAIL because PolicyApprover still accepts a boolean prompt and Registry passes no identity.

- [ ] **Step 3: Refactor PolicyApprover**

```python
# core of src/scout/permissions.py
class PolicyApprover:
    def __init__(
        self,
        mode: str = "ask",
        gateway: ApprovalGateway | None = None,
        emit: Emitter | None = None,
    ) -> None:
        self.mode = mode
        self.gateway = gateway
        self.emit = emit
        self._session_allow: dict[str, set[str]] = {}

    def check(
        self,
        tool: Tool,
        args: dict[str, Any],
        *,
        session_id: str,
        run_id: str,
    ) -> Decision:
        if tool.risk == Risk.SAFE:
            return Decision(True)
        if self.mode == "readonly":
            return Decision(False, f"当前是只读模式，{tool.name} 会产生副作用，已拒绝")
        if self.mode == "auto" or tool.name in self._session_allow.get(session_id, set()):
            return Decision(True)
        if self.gateway is None:
            return Decision(False, f"当前无交互审批通道，拒绝执行 {tool.name}")

        request = ApprovalRequest.create(
            run_id,
            session_id,
            ApprovalKind.TOOL,
            f"执行工具 {tool.name}",
            {"tool": tool.name, "arguments": args, "risk": int(tool.risk)},
        )
        decision = self.gateway.request(request, self.emit)
        if decision.action is ApprovalAction.CANCEL:
            raise RunCancelled("用户取消运行")
        if decision.action is ApprovalAction.ALLOW_SESSION:
            self._session_allow.setdefault(session_id, set()).add(tool.name)
            return Decision(True)
        if decision.action is ApprovalAction.APPROVE:
            return Decision(True)
        reason = decision.feedback or f"用户拒绝执行 {tool.name}，请换一种方式或询问用户"
        return Decision(False, reason)

    def clear_session(self, session_id: str) -> None:
        self._session_allow.pop(session_id, None)
```

- [ ] **Step 4: Pass identity from ToolRegistry**

```python
# src/scout/tools/registry.py inside execute()
session_id = getattr(self.ctx.session, "id", "")
decision = self.approver.check(
    tool,
    call.arguments,
    session_id=session_id,
    run_id=self.ctx.run_id,
)
```

Keep unknown-tool behavior, timing, truncation, and parallel SAFE execution unchanged.

- [ ] **Step 5: Run registry regression**

Run: `uv run pytest tests/test_approval.py tests/test_registry.py -v`

Expected: all tests PASS, including parallel SAFE tools.

- [ ] **Step 6: Commit if explicitly authorized**

```bash
git add src/scout/permissions.py src/scout/tools/registry.py tests/test_approval.py tests/test_registry.py
git commit -m "$(cat <<'EOF'
feat: route risky tools through approval gateway

EOF
)"
```

---

### Task 3: Gate the first plan and defer sibling tools

**Files:**
- Create: `tests/test_plan_gate.py`
- Modify: `src/scout/core/agent.py`
- Modify: `tests/conftest.py`
- Modify: `tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `ApprovalGateway`, `RunCancellation`, `PolicyApprover`.
- Changes: `Agent.run(user_input, stream=True, run_id=None) -> AgentResult`.
- Produces: `AgentResult.stop_reason == "cancelled"` when cancellation wins.

- [ ] **Step 1: Add a scripted gateway helper**

```python
# append to tests/conftest.py
from scout.approval import ApprovalDecision


class ScriptedGateway:
    def __init__(self, decisions: list[ApprovalDecision]):
        self.decisions = list(decisions)
        self.requests = []

    def request(self, request, emit=None):
        self.requests.append(request)
        if emit:
            emit("approval_required", request.event_data())
        decision = self.decisions.pop(0)
        if emit:
            emit(
                "approval_resolved",
                {
                    "approval_id": request.id,
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "action": decision.action.value,
                },
            )
        return decision
```

- [ ] **Step 2: Write failing plan-gate tests**

```python
# tests/test_plan_gate.py
from conftest import FakeLLM, ScriptedGateway

from scout.approval import ApprovalAction, ApprovalDecision, ApprovalKind
from scout.llm.base import Message, ToolCall
from scout.runtime import Runtime


def test_plan_and_sibling_tool_batch_executes_only_plan(settings):
    gateway = ScriptedGateway([ApprovalDecision(ApprovalAction.APPROVE)])
    first = Message(
        role="assistant",
        tool_calls=[
            ToolCall("p1", "update_plan", {"steps": ["inspect", "report"], "current": 1}),
            ToolCall("f1", "list_dir", {"path": "."}),
        ],
    )
    llm = FakeLLM([first, Message(role="assistant", content="done")])
    runtime = Runtime(settings, llm=llm, approval_gateway=gateway, enable_trace=False)
    session = runtime.new_session()
    try:
        runtime.build_agent(session).run("research", stream=False)
    finally:
        runtime.close()

    assert gateway.requests[0].kind is ApprovalKind.PLAN
    tool_messages = [m for m in llm.received[1] if m.role == "tool"]
    assert {m.tool_call_id for m in tool_messages} == {"p1", "f1"}
    assert "计划尚未确认" in next(m.content for m in tool_messages if m.tool_call_id == "f1")


def test_revise_feedback_requires_a_second_plan_confirmation(settings):
    gateway = ScriptedGateway(
        [
            ApprovalDecision(ApprovalAction.REVISE, "add source validation"),
            ApprovalDecision(ApprovalAction.APPROVE),
        ]
    )
    llm = FakeLLM(
        [
            Message(role="assistant", tool_calls=[
                ToolCall("p1", "update_plan", {"steps": ["inspect"], "current": 1})
            ]),
            Message(role="assistant", tool_calls=[
                ToolCall("p2", "update_plan", {"steps": ["inspect", "validate"], "current": 1})
            ]),
            Message(role="assistant", content="done"),
        ]
    )
    runtime = Runtime(settings, llm=llm, approval_gateway=gateway, enable_trace=False)
    session = runtime.new_session()
    try:
        runtime.build_agent(session).run("research", stream=False)
    finally:
        runtime.close()

    assert len(gateway.requests) == 2
    assert "add source validation" in next(
        m.content for m in llm.received[1] if m.tool_call_id == "p1"
    )
```

- [ ] **Step 3: Run tests and verify tools currently execute together**

Run: `uv run pytest tests/test_plan_gate.py -v`

Expected: FAIL because Runtime/Agent do not accept a gateway and `_run_tools` executes the sibling tool.

- [ ] **Step 4: Add run-scoped state and cancellation handling**

In `Agent.__init__`:

```python
approval_gateway: ApprovalGateway | None = None,
cancellation: RunCancellation | None = None,
```

Store them as `self.approval_gateway` and `self.cancellation or RunCancellation()`.

At the start of `run`:

```python
run_id = run_id or uuid.uuid4().hex[:8]
self.cancellation.clear()
self.registry.ctx.run_id = run_id
self.registry.ctx.cancellation = self.cancellation
plan_confirmed = self.role != "lead" or self.approval_gateway is None
```

At each Loop step and before tool execution:

```python
self.cancellation.ensure_active()
```

Catch `RunCancelled` before the generic exception:

```python
except RunCancelled:
    stop_reason = "cancelled"
    final_text = "运行已取消。"
```

- [ ] **Step 5: Partition the first plan batch and request approval**

Refactor `_run_tools` to accept `run_id` and mutable run state, or introduce a small `_RunState` dataclass. The core branch must be:

```python
plan_calls = [
    call for call in runnable
    if call.name == "update_plan"
] if self.role == "lead" and not state.plan_confirmed else []

deferred: dict[str, str] = {}
if plan_calls:
    allowed_ids = {call.id for call in plan_calls}
    for call in runnable:
        if call.id not in allowed_ids:
            deferred[call.id] = "计划尚未确认，请在确认后重新调用该工具。"
    runnable = plan_calls

results = self.registry.execute_batch(runnable) if runnable else []

if plan_calls and any(result.ok for result in results):
    request = ApprovalRequest.create(
        run_id,
        self.session.id,
        ApprovalKind.PLAN,
        "调研计划待确认",
        {"plan": self.session.plan.render(), "steps": self.session.plan.steps},
    )
    decision = self.approval_gateway.request(
        request,
        lambda kind, data: self.bus.emit(kind, data, agent=self.name),
    )
    if decision.action is ApprovalAction.APPROVE:
        state.plan_confirmed = True
    elif decision.action is ApprovalAction.CANCEL:
        raise RunCancelled("用户取消运行")
    else:
        feedback = decision.feedback or "请重新制定计划"
        plan_feedback = f"[计划未确认] 用户修改意见：{feedback}\n请重拟计划并再次提交。"
```

When assembling messages, merge `blocked`, `deferred`, actual results, and `plan_feedback`, preserving one tool message per original call. Do not emit `TOOL_START` for deferred calls.

- [ ] **Step 6: Add no-repeat and worker assertions**

Add tests that:

- an approved plan's later `current` update does not call the gateway again;
- a lead Agent with no `update_plan` never calls the gateway;
- a worker Agent never requests plan approval;
- auto mode with no gateway preserves all existing tests.

Run: `uv run pytest tests/test_plan_gate.py tests/test_agent_loop.py -v`

Expected: all selected tests PASS.

- [ ] **Step 7: Commit if explicitly authorized**

```bash
git add src/scout/core/agent.py tests/conftest.py tests/test_plan_gate.py tests/test_agent_loop.py
git commit -m "$(cat <<'EOF'
feat: pause agent execution for plan confirmation

EOF
)"
```

---

### Task 4: Wire cancellation through Agent and page fetching

**Files:**
- Modify: `tests/test_cancellation.py`
- Modify: `src/scout/core/agent.py`
- Modify: `src/scout/runtime.py`
- Modify: `src/scout/content/page_fetcher.py`

**Interfaces:**
- Consumes: Task 1 `RunCancellation`.
- Produces: `Runtime.build_agent(session, cancellation=None)`.
- Ensures: browser fallback checks cancellation before rendering.

- [ ] **Step 1: Write failing Agent cancellation tests**

```python
# append to tests/test_cancellation.py
from conftest import FakeLLM

from scout.core.events import EventType
from scout.llm.base import Message
from scout.runtime import Runtime


def test_cancel_before_first_step_emits_cancelled_run_end(settings):
    llm = FakeLLM([Message(role="assistant", content="must not run")])
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    token = RunCancellation()
    token.request()
    seen = []
    runtime.bus.subscribe(lambda event: seen.append(event))
    try:
        result = runtime.build_agent(session, cancellation=token).run(
            "question", stream=False, reset_cancellation=False
        )
    finally:
        runtime.close()

    assert result.stop_reason == "cancelled"
    assert not llm.received
    assert next(e for e in seen if e.type is EventType.RUN_END).data["stop_reason"] == "cancelled"
```

Add `reset_cancellation: bool = True` to `Agent.run`. At run start call `self.cancellation.clear()` only when this flag is true. Normal CLI and Web runs use the default; the pre-cancelled unit test passes `reset_cancellation=False`.

- [ ] **Step 2: Run and verify the new signature is absent**

Run: `uv run pytest tests/test_cancellation.py -v`

Expected: FAIL because Runtime/Agent do not expose cancellation injection.

- [ ] **Step 3: Inject one token per run**

Change Runtime:

```python
def build_agent(
    self,
    session: Session,
    cancellation: RunCancellation | None = None,
) -> Agent:
    token = cancellation or RunCancellation()
    ctx = ToolContext(
        workspace=self.settings.workspace,
        settings=self.settings,
        llm=self.llm,
        memory=self.memory,
        evidence=session.evidence,
        session=session,
        emit=lambda name, data: self.bus.emit(name, data),
        cancellation=token,
        page_fetcher=self.page_fetcher,
    )
    registry = build_registry(ctx, self.approver, self.settings.parallel_tool_calls)
    agent = Agent(
        llm=self.llm,
        settings=self.settings,
        session=session,
        registry=registry,
        bus=self.bus,
        memory=self.memory,
        approval_gateway=self.approval_gateway,
        cancellation=token,
    )
    ctx.spawn = agent.make_spawner()
    return agent
```

Ensure the same token reaches Agent and PageFetcher through ToolContext.

- [ ] **Step 4: Verify cancellation boundaries**

Add tests for:

- cancelled before LLM call;
- cancelled after a tool completes prevents the next LLM step;
- cancelled before browser fallback means `FakeBrowser.render` is not called;
- `RUN_END` includes `stop_reason=cancelled`.

Run: `uv run pytest tests/test_cancellation.py tests/test_page_fetcher.py -v`

Expected: all selected tests PASS.

- [ ] **Step 5: Commit if explicitly authorized**

```bash
git add src/scout/core/agent.py src/scout/runtime.py src/scout/content/page_fetcher.py tests/test_cancellation.py tests/test_page_fetcher.py
git commit -m "$(cat <<'EOF'
feat: support cooperative agent cancellation

EOF
)"
```

---

### Task 5: Add the CLI gateway and preserve command behavior

**Files:**
- Create: `src/scout/approval_cli.py`
- Modify: `src/scout/cli.py`
- Modify: `src/scout/runtime.py`
- Modify: `src/scout/core/prompts.py`
- Create: `tests/test_approval_cli.py`
- Modify: `tests/test_agent_loop.py`

**Interfaces:**
- Produces: `CliApprovalGateway(console).request(request) -> ApprovalDecision`.
- Changes: `Runtime(..., approval_gateway=None)` while preserving `approver=` test override.
- CLI ask/readonly inject a gateway; auto does not.

- [ ] **Step 1: Write failing CLI input mapping tests**

```python
# tests/test_approval_cli.py
from scout.approval import ApprovalAction, ApprovalKind, ApprovalRequest
from scout.approval_cli import CliApprovalGateway


class FakeConsole:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.printed = []

    def print(self, *values, **kwargs):
        self.printed.append(values)

    def input(self, prompt):
        return next(self.answers)


def request(kind):
    return ApprovalRequest.create("r", "s", kind, "Confirm", {"plan": "[ ] 1. Search"})


def test_plan_edit_collects_feedback():
    gateway = CliApprovalGateway(FakeConsole(["e", "Add source validation"]))
    decision = gateway.request(request(ApprovalKind.PLAN))
    assert decision.action is ApprovalAction.REVISE
    assert decision.feedback == "Add source validation"


def test_tool_allow_session_mapping():
    gateway = CliApprovalGateway(FakeConsole(["a"]))
    decision = gateway.request(request(ApprovalKind.TOOL))
    assert decision.action is ApprovalAction.ALLOW_SESSION
```

- [ ] **Step 2: Run tests and verify the gateway is absent**

Run: `uv run pytest tests/test_approval_cli.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement CLI gateway choices**

```python
# src/scout/approval_cli.py
from __future__ import annotations

from rich.panel import Panel

from .approval import ApprovalAction, ApprovalDecision, ApprovalKind, ApprovalRequest


class CliApprovalGateway:
    def __init__(self, console) -> None:
        self.console = console

    def request(self, request: ApprovalRequest, emit=None) -> ApprovalDecision:
        if emit:
            emit("approval_required", request.event_data())

        def finish(decision: ApprovalDecision) -> ApprovalDecision:
            if emit:
                emit(
                    "approval_resolved",
                    {
                        "approval_id": request.id,
                        "run_id": request.run_id,
                        "session_id": request.session_id,
                        "action": decision.action.value,
                    },
                )
            return decision

        body = request.payload.get("plan") or self._tool_body(request.payload)
        self.console.print()
        self.console.print(Panel(str(body), title=request.title, border_style="yellow"))
        if request.kind is ApprovalKind.PLAN:
            answer = self.console.input(
                "[yellow](y=确认 / e=修改意见 / c=取消运行) [/yellow]"
            ).strip().lower()
            if answer == "e":
                feedback = self.console.input("[yellow]修改意见：[/yellow]").strip()
                return finish(ApprovalDecision(ApprovalAction.REVISE, feedback))
            if answer == "c":
                return finish(ApprovalDecision(ApprovalAction.CANCEL))
            return finish(ApprovalDecision(ApprovalAction.APPROVE))

        answer = self.console.input(
            "[yellow](y=允许一次 / n=拒绝 / a=本会话允许 / c=取消运行) [/yellow]"
        ).strip().lower()
        if answer == "a":
            return finish(ApprovalDecision(ApprovalAction.ALLOW_SESSION))
        if answer == "c":
            return finish(ApprovalDecision(ApprovalAction.CANCEL))
        if answer in {"n", "no"}:
            return finish(ApprovalDecision(ApprovalAction.REJECT))
        return finish(ApprovalDecision(ApprovalAction.APPROVE))

    @staticmethod
    def _tool_body(payload: dict) -> str:
        return f"{payload.get('tool', '')}\n{payload.get('arguments', {})}"
```

- [ ] **Step 4: Assemble Runtime and CLI**

Extend Runtime constructor:

```python
approval_gateway: ApprovalGateway | None = None,
```

Set:

```python
self.approval_gateway = approval_gateway
self.approver = approver or PolicyApprover(
    settings.permission_mode,
    gateway=approval_gateway,
    emit=lambda kind, data: self.bus.emit(kind, data),
)
```

Pass `approval_gateway` into lead Agents, but pass `None` into worker Agents. Session grants remain process-local and are never written to SQLite. In the CLI `/resume` branch, call `runtime.approver.clear_session(session_id)` before building the resumed Agent; a process restart also clears all grants. Do not clear grants from generic `Runtime.resume_session`, because the Web adapter uses that method between turns of the same live session.

In CLI:

```python
gateway = None if settings.permission_mode == "auto" else CliApprovalGateway(console)
runtime = Runtime(settings, bus=bus, approval_gateway=gateway)
```

Delete the old `make_approver` closure and its bool prompt. Keep all command parsing and renderer behavior.

- [ ] **Step 5: Clarify first-plan prompt behavior**

Add one sentence to the lead system prompt near planning guidance:

```text
首次制定计划时请单独调用 update_plan，等待用户确认后再发起搜索、抓取或其他工具。
```

- [ ] **Step 6: Run complete Python regression**

Run: `uv run pytest tests -q && uv run ruff check src tests`

Expected: full suite PASS and Ruff exits 0.

- [ ] **Step 7: Manually smoke the three permission modes**

Run:

```bash
uv run scout --workspace /tmp/scout-hitl "调研 SQLite 与 DuckDB 的差异"
uv run scout --auto --workspace /tmp/scout-hitl-auto "列出工作区文件"
uv run scout --readonly --workspace /tmp/scout-hitl-ro "制定一个调研计划"
```

Expected:

- default ask pauses on the first plan;
- auto shows no approval prompt;
- readonly confirms the plan but rejects CAUTION/DANGEROUS tools.

- [ ] **Step 8: Commit if explicitly authorized**

```bash
git add src/scout/approval_cli.py src/scout/cli.py src/scout/runtime.py src/scout/core/prompts.py tests
git commit -m "$(cat <<'EOF'
feat: add CLI plan and tool approvals

EOF
)"
```

## Plan Completion Check

- Run `uv run pytest tests -q`.
- Run `uv run ruff check src tests`.
- Confirm the first plan defers sibling tools and each call still receives a tool message.
- Confirm revise feedback reaches the next LLM turn.
- Confirm later plan progress updates do not prompt.
- Confirm session grants do not cross sessions or survive resume.
- Confirm cancellation ends with `stop_reason=cancelled`.
