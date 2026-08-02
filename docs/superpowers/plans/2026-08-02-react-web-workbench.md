# React Web Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local single-user FastAPI + React/Vite workbench for sessions, streaming runs, plans, sources, approvals, cancellation, and reports.

**Architecture:** FastAPI owns a single Runtime and `RunManager`; Agent execution happens in a background thread. EventBus events are correlated with the active run, buffered with monotonic IDs, and streamed through SSE. React uses a reducer and native fetch/EventSource without a global state library.

**Tech Stack:** Python 3.11+, FastAPI, Uvicorn, Pydantic, React, TypeScript, Vite, Vitest, Testing Library, Playwright E2E.

## Global Constraints

- Execute after both `2026-08-02-content-extraction-arxiv.md` and `2026-08-02-human-in-the-loop-core.md`.
- Bind to `127.0.0.1` by default; no login, public hosting, multi-user, or multi-tenant support.
- Allow one active lead run per process; a second run returns HTTP 409.
- Core modules never import FastAPI or React.
- SQLite remains the durable source for messages, plan, sources, and usage; SSE buffers are in memory only.
- SSE envelopes always include `id`, `type`, `run_id`, `session_id`, `ts`, `agent`, and `data`.
- Browser refresh restores the Session snapshot and reconnects to an active run.
- Use React state/reducer only; do not add Redux, Zustand, or another store.
- Install package versions through uv/npm rather than inventing version pins.
- Commit steps are executed only if the user explicitly authorizes commits.

## File Map

### Python

- Create `src/scout/web/__init__.py`: package marker.
- Create `src/scout/web/schemas.py`: Pydantic API models.
- Create `src/scout/web/run_manager.py`: active-run thread, event buffer, cancellation.
- Create `src/scout/web/gateway.py`: blocking Web approval gateway.
- Create `src/scout/web/sse.py`: SSE encoding and heartbeat generator.
- Create `src/scout/web/routes.py`: health, sessions, runs, events, approvals.
- Create `src/scout/web/app.py`: app factory, lifespan, static frontend.
- Create `src/scout/web_cli.py`: `scout-web` command.
- Modify `src/scout/memory/store.py`: read/update Session meta.
- Modify `src/scout/core/session.py`: persist/restore plan and usage in meta.
- Modify `src/scout/tools/plan.py`: persist plan immediately.
- Modify `src/scout/runtime.py`: Session snapshot helper.
- Modify `pyproject.toml` and `uv.lock`: Web extra, script, frontend wheel inclusion.
- Create `tests/test_session_state.py`, `tests/test_run_manager.py`, `tests/test_web_gateway.py`, `tests/test_web_api.py`.

### React

- Create `web/package.json`, Vite/TypeScript config, `web/index.html`.
- Create `web/src/api/types.ts`, `client.ts`, `sse.ts`.
- Create `web/src/state/runReducer.ts`, `useWorkbench.ts`.
- Create `web/src/components/TopBar.tsx`, `SessionList.tsx`, `PlanPanel.tsx`, `ChatPanel.tsx`, `EventPanel.tsx`, `SourcesPanel.tsx`, `ApprovalModal.tsx`.
- Create `web/src/App.tsx`, `main.tsx`, `styles.css`.
- Create reducer/component tests and `web/e2e/workbench.spec.ts`.
- Modify `.gitignore`: ignore `web/node_modules`, `web/test-results`, and `.superpowers/`; keep `web/dist` available for wheel packaging.

---

### Task 1: Persist Session plan and usage in existing metadata

**Files:**
- Create: `tests/test_session_state.py`
- Modify: `src/scout/memory/store.py`
- Modify: `src/scout/core/session.py`
- Modify: `src/scout/tools/plan.py`
- Modify: `src/scout/core/agent.py`
- Modify: `src/scout/runtime.py`

**Interfaces:**
- Produces: `Store.get_session(session_id) -> dict | None`.
- Produces: `Store.update_session_meta(session_id, meta)`.
- Produces: `Session.persist_state()`.
- Produces: `Runtime.session_snapshot(session_id) -> dict`.

- [ ] **Step 1: Write failing persistence and snapshot tests**

```python
# tests/test_session_state.py
from scout.llm.base import Usage
from scout.runtime import Runtime
from scout.tools.plan import Plan


def test_session_restores_plan_and_usage(settings, fake_llm):
    runtime = Runtime(settings, llm=fake_llm, enable_trace=False)
    session = runtime.new_session("Research")
    session.plan = Plan(["search", "validate"], current=2)
    session.usage = Usage(prompt_tokens=30, completion_tokens=10, cached_tokens=5, calls=2)
    session.persist_state()

    resumed = runtime.resume_session(session.id)
    snapshot = runtime.session_snapshot(session.id)
    runtime.close()

    assert resumed.plan.steps == ["search", "validate"]
    assert resumed.plan.current == 2
    assert resumed.usage.prompt_tokens == 30
    assert snapshot["plan_steps"] == ["search", "validate"]
    assert snapshot["usage"]["calls"] == 2
```

- [ ] **Step 2: Run the test and confirm metadata APIs are missing**

Run: `uv run pytest tests/test_session_state.py -v`

Expected: FAIL because `persist_state` and `session_snapshot` do not exist.

- [ ] **Step 3: Add Store metadata methods**

```python
# src/scout/memory/store.py
def get_session(self, session_id: str) -> dict[str, Any] | None:
    rows = self._query(
        """SELECT s.id, s.title, s.created_at, s.updated_at, s.meta,
                  (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS message_count
           FROM sessions s WHERE s.id=?""",
        (session_id,),
    )
    if not rows:
        return None
    result = dict(rows[0])
    result["meta"] = json.loads(result["meta"] or "{}")
    return result


def update_session_meta(self, session_id: str, meta: dict[str, Any]) -> None:
    self._execute(
        "UPDATE sessions SET meta=?, updated_at=? WHERE id=?",
        (json.dumps(meta, ensure_ascii=False), time.time(), session_id),
    )
```

- [ ] **Step 4: Persist and restore Session state**

```python
# methods/helpers in src/scout/core/session.py
def persist_state(self) -> None:
    stored = self.store.get_session(self.id) or {}
    meta = dict(stored.get("meta", {}))
    meta.update(
        {
            "plan": {"steps": self.plan.steps, "current": self.plan.current},
            "usage": {
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "cached_tokens": self.usage.cached_tokens,
                "calls": self.usage.calls,
            },
        }
    )
    self.store.update_session_meta(
        self.id,
        meta,
    )


@staticmethod
def _state_from_meta(meta: dict) -> tuple[Plan, Usage]:
    plan_data = meta.get("plan", {})
    usage_data = meta.get("usage", {})
    return (
        Plan(list(plan_data.get("steps", [])), int(plan_data.get("current", 0))),
        Usage(
            prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
            completion_tokens=int(usage_data.get("completion_tokens", 0)),
            cached_tokens=int(usage_data.get("cached_tokens", 0)),
            calls=int(usage_data.get("calls", 0)),
        ),
    )
```

Change `Session.resume` to call `store.get_session`, parse the returned `meta`, and initialize `plan`/`usage`. In `update_plan`, call `ctx.session.persist_state()` immediately after assigning `ctx.session.plan`.

At Agent run completion, update usage before persistence:

```python
self.session.usage = self.session.usage + usage
self.session.persist(pending)
self.session.persist_state()
```

- [ ] **Step 5: Add Runtime snapshot**

```python
# src/scout/runtime.py
def session_snapshot(self, session_id: str) -> dict[str, Any]:
    session = self.resume_session(session_id)
    return {
        "id": session.id,
        "title": session.title,
        "messages": [message.to_dict() for message in session.working.messages],
        "plan": session.plan.render(),
        "plan_steps": session.plan.steps,
        "plan_current": session.plan.current,
        "sources": session.evidence.sources(),
        "usage": {
            "calls": session.usage.calls,
            "prompt_tokens": session.usage.prompt_tokens,
            "completion_tokens": session.usage.completion_tokens,
            "cached_tokens": session.usage.cached_tokens,
        },
    }
```

- [ ] **Step 6: Run persistence regression**

Run: `uv run pytest tests/test_session_state.py tests/test_agent_loop.py -v`

Expected: all tests PASS, including conversation resume.

- [ ] **Step 7: Commit if explicitly authorized**

```bash
git add src/scout/memory/store.py src/scout/core/session.py src/scout/tools/plan.py src/scout/core/agent.py src/scout/runtime.py tests/test_session_state.py
git commit -m "$(cat <<'EOF'
feat: persist session plan and usage state

EOF
)"
```

---

### Task 2: Implement RunManager and replayable event buffering

**Files:**
- Create: `src/scout/web/__init__.py`
- Create: `src/scout/web/run_manager.py`
- Create: `tests/test_run_manager.py`

**Interfaces:**
- Produces: `RunEnvelope`.
- Produces: `RunRecord`.
- Produces: `RunManager.start_run(session_id, question) -> RunRecord`.
- Produces: `events_after(run_id, after_id)`, `wait_for_events(...)`, `cancel(...)`, `shutdown()`.

- [ ] **Step 1: Write failing buffering and single-run tests**

```python
# tests/test_run_manager.py
import threading

import pytest

from scout.core.events import EventBus, EventType
from scout.web.run_manager import ActiveRunError, RunManager


class BlockingAgent:
    def __init__(self, bus, release):
        self.bus = bus
        self.release = release

    def run(self, question, stream=True, run_id=None, **kwargs):
        self.bus.emit(EventType.RUN_START, {"run_id": run_id, "input": question})
        self.bus.emit(EventType.LLM_DELTA, {"text": "hello"})
        self.release.wait(2)
        self.bus.emit(EventType.RUN_END, {"run_id": run_id, "stop_reason": "completed"})


class FakeRuntime:
    def __init__(self):
        self.bus = EventBus()
        self.release = threading.Event()

    def resume_session(self, session_id):
        return type("Session", (), {"id": session_id})()

    def build_agent(self, session, cancellation=None):
        return BlockingAgent(self.bus, self.release)


def test_buffers_events_with_monotonic_ids_and_rejects_second_run():
    runtime = FakeRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")
    manager.wait_for_events(record.run_id, after_id=0, timeout=1)

    events = manager.events_after(record.run_id, 0)
    assert [event.id for event in events] == list(range(1, len(events) + 1))
    assert all(event.run_id == record.run_id for event in events)
    with pytest.raises(ActiveRunError):
        manager.start_run("s2", "second")

    runtime.release.set()
    record.thread.join(2)
```

- [ ] **Step 2: Run and verify Web package is missing**

Run: `uv run pytest tests/test_run_manager.py -v`

Expected: FAIL with `ModuleNotFoundError: scout.web`.

- [ ] **Step 3: Implement records and event envelopes**

```python
# src/scout/web/run_manager.py
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Literal

from ..cancellation import RunCancellation
from ..core.events import Event


class ActiveRunError(RuntimeError):
    pass


class RunNotFoundError(KeyError):
    pass


@dataclass(slots=True)
class RunEnvelope:
    id: int
    type: str
    run_id: str
    session_id: str
    ts: float
    agent: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "ts": self.ts,
            "agent": self.agent,
            "data": self.data,
        }


@dataclass
class RunRecord:
    run_id: str
    session_id: str
    cancellation: RunCancellation
    status: Literal["running", "awaiting_approval", "cancelling", "finished"] = "running"
    started_at: float = field(default_factory=time.time)
    buffer: deque[RunEnvelope] = field(default_factory=lambda: deque(maxlen=500))
    next_event_id: int = 1
    thread: threading.Thread | None = None
    condition: threading.Condition = field(default_factory=threading.Condition)
```

- [ ] **Step 4: Implement RunManager lifecycle**

```python
class RunManager:
    def __init__(self, runtime, approval_gateway=None) -> None:
        self.runtime = runtime
        self.approval_gateway = approval_gateway
        self._lock = threading.RLock()
        self._active: RunRecord | None = None
        self._records: dict[str, RunRecord] = {}
        runtime.bus.subscribe(self.on_event)

    def start_run(self, session_id: str, question: str) -> RunRecord:
        with self._lock:
            if self._active and self._active.status != "finished":
                raise ActiveRunError("已有任务正在运行")
            record = RunRecord(uuid.uuid4().hex[:12], session_id, RunCancellation())
            self._active = record
            self._records[record.run_id] = record
            record.thread = threading.Thread(
                target=self._run,
                args=(record, question),
                name=f"scout-run-{record.run_id}",
                daemon=True,
            )
            record.thread.start()
            return record

    def _run(self, record: RunRecord, question: str) -> None:
        try:
            session = self.runtime.resume_session(record.session_id)
            agent = self.runtime.build_agent(session, cancellation=record.cancellation)
            agent.run(question, stream=True, run_id=record.run_id)
        finally:
            with record.condition:
                record.status = "finished"
                record.condition.notify_all()
            with self._lock:
                if self._active is record:
                    self._active = None

    def on_event(self, event: Event) -> None:
        with self._lock:
            record = self._active
        if record is None:
            return
        with record.condition:
            envelope = RunEnvelope(
                record.next_event_id,
                event.type.value,
                record.run_id,
                record.session_id,
                event.ts,
                event.agent,
                event.data,
            )
            record.next_event_id += 1
            record.buffer.append(envelope)
            record.condition.notify_all()

    def events_after(self, run_id: str, after_id: int) -> list[RunEnvelope]:
        record = self._records.get(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        with record.condition:
            return [event for event in record.buffer if event.id > after_id]

    def wait_for_events(self, run_id: str, after_id: int, timeout: float = 15) -> bool:
        record = self._records.get(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        with record.condition:
            return record.condition.wait_for(
                lambda: any(event.id > after_id for event in record.buffer)
                or record.status == "finished",
                timeout,
            )

    def cancel(self, run_id: str) -> str:
        record = self._records.get(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        if record.status == "finished":
            return "already_finished"
        record.status = "cancelling"
        record.cancellation.request()
        if self.approval_gateway:
            self.approval_gateway.cancel_run(run_id)
        return "cancelling"

    def shutdown(self) -> None:
        with self._lock:
            active = self._active
        if active:
            self.cancel(active.run_id)
            if active.thread:
                active.thread.join(timeout=5)
```

- [ ] **Step 5: Add replay, completion, and cancellation tests**

Test that `events_after(run_id, N)` excludes IDs `<= N`, `cancel` sets the token, and finished records remain replayable until evicted. Limit `_records` to the latest 20 records when inserting a new run.

Run: `uv run pytest tests/test_run_manager.py -v`

Expected: all RunManager tests PASS.

- [ ] **Step 6: Commit if explicitly authorized**

```bash
git add src/scout/web tests/test_run_manager.py
git commit -m "$(cat <<'EOF'
feat: add single-run event manager

EOF
)"
```

---

### Task 3: Add blocking WebApprovalGateway

**Files:**
- Create: `src/scout/web/gateway.py`
- Create: `tests/test_web_gateway.py`
- Modify: `src/scout/web/run_manager.py`

**Interfaces:**
- Consumes: core ApprovalGateway protocol.
- Produces: `WebApprovalGateway.request`, `resolve`, `cancel_run`, `pending_for_run`.
- Produces: `ApprovalNotFoundError`, `ApprovalAlreadyResolvedError`.

- [ ] **Step 1: Write failing resolve and cancel tests**

```python
# tests/test_web_gateway.py
import threading
import time

import pytest

from scout.approval import ApprovalAction, ApprovalDecision, ApprovalKind, ApprovalRequest
from scout.web.gateway import (
    ApprovalAlreadyResolvedError,
    ApprovalNotFoundError,
    WebApprovalGateway,
)


def test_request_blocks_until_resolved():
    gateway = WebApprovalGateway()
    request = ApprovalRequest.create("r1", "s1", ApprovalKind.PLAN, "Plan", {})
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", gateway.request(request)))
    thread.start()
    while not gateway.pending_for_run("r1"):
        time.sleep(0.01)

    gateway.resolve(request.id, ApprovalDecision(ApprovalAction.APPROVE))
    thread.join(1)

    assert result["value"].action is ApprovalAction.APPROVE
    with pytest.raises(ApprovalAlreadyResolvedError):
        gateway.resolve(request.id, ApprovalDecision(ApprovalAction.REJECT))


def test_cancel_run_unblocks_pending_request():
    gateway = WebApprovalGateway()
    request = ApprovalRequest.create("r1", "s1", ApprovalKind.TOOL, "Tool", {})
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", gateway.request(request)))
    thread.start()
    while not gateway.pending_for_run("r1"):
        time.sleep(0.01)
    gateway.cancel_run("r1")
    thread.join(1)
    assert result["value"].action is ApprovalAction.CANCEL
```

- [ ] **Step 2: Run and confirm gateway module is missing**

Run: `uv run pytest tests/test_web_gateway.py -v`

Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement pending approvals**

```python
# src/scout/web/gateway.py
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..approval import ApprovalAction, ApprovalDecision, ApprovalRequest


class ApprovalNotFoundError(KeyError):
    pass


class ApprovalAlreadyResolvedError(RuntimeError):
    pass


@dataclass
class _Pending:
    request: ApprovalRequest
    event: threading.Event = field(default_factory=threading.Event)
    decision: ApprovalDecision | None = None
    resolved: bool = False


class WebApprovalGateway:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, _Pending] = {}
        self._resolved: set[str] = set()

    def request(self, request: ApprovalRequest, emit=None) -> ApprovalDecision:
        item = _Pending(request)
        with self._lock:
            self._pending[request.id] = item
        if emit:
            emit("approval_required", request.event_data())
        item.event.wait()
        with self._lock:
            self._pending.pop(request.id, None)
            self._resolved.add(request.id)
        decision = item.decision or ApprovalDecision(ApprovalAction.CANCEL)
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

    def resolve(self, approval_id: str, decision: ApprovalDecision) -> None:
        with self._lock:
            if approval_id in self._resolved:
                raise ApprovalAlreadyResolvedError(approval_id)
            item = self._pending.get(approval_id)
            if item is None:
                raise ApprovalNotFoundError(approval_id)
            if item.resolved:
                raise ApprovalAlreadyResolvedError(approval_id)
            item.resolved = True
            item.decision = decision
            item.event.set()

    def cancel_run(self, run_id: str) -> None:
        with self._lock:
            items = [p for p in self._pending.values() if p.request.run_id == run_id]
        for item in items:
            try:
                self.resolve(item.request.id, ApprovalDecision(ApprovalAction.CANCEL))
            except ApprovalAlreadyResolvedError:
                continue

    def pending_for_run(self, run_id: str) -> list[ApprovalRequest]:
        with self._lock:
            return [
                item.request
                for item in self._pending.values()
                if item.request.run_id == run_id and not item.resolved
            ]
```

- [ ] **Step 4: Make RunManager status reflect pending approvals**

When `RunManager.on_event` sees `approval_required`, set status to `awaiting_approval`; on `approval_resolved`, set it to `running` unless cancellation is active.

Run: `uv run pytest tests/test_web_gateway.py tests/test_run_manager.py -v`

Expected: all tests PASS.

- [ ] **Step 5: Commit if explicitly authorized**

```bash
git add src/scout/web/gateway.py src/scout/web/run_manager.py tests/test_web_gateway.py tests/test_run_manager.py
git commit -m "$(cat <<'EOF'
feat: add web approval gateway

EOF
)"
```

---

### Task 4: Expose FastAPI REST and SSE endpoints

**Files:**
- Create: `src/scout/web/schemas.py`
- Create: `src/scout/web/sse.py`
- Create: `src/scout/web/routes.py`
- Create: `src/scout/web/app.py`
- Create: `src/scout/web_cli.py`
- Create: `tests/test_web_api.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Produces all `/api` endpoints in the approved spec.
- Produces `create_app(settings=None, runtime=None)`.
- Produces `scout-web` CLI.

- [ ] **Step 1: Add Web dependencies through uv**

Run:

```bash
uv add --optional web fastapi "uvicorn[standard]"
```

Expected: `pyproject.toml` and `uv.lock` update successfully.

- [ ] **Step 2: Write failing health, session, concurrency, and approval API tests**

```python
# tests/test_web_api.py
from fastapi.testclient import TestClient

from conftest import FakeLLM
from scout.llm.base import Message, ToolCall
from scout.runtime import Runtime
from scout.web.app import create_app


def make_client(settings, script):
    runtime = Runtime(settings, llm=FakeLLM(script), enable_trace=False)
    return TestClient(create_app(settings=settings, runtime=runtime))


def test_health_and_session_creation(settings):
    with make_client(settings, []) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        created = client.post("/api/sessions", json={"title": "Research"})
        assert created.status_code == 201
        detail = client.get(f"/api/sessions/{created.json()['id']}")
        assert detail.status_code == 200
        assert detail.json()["title"] == "Research"


def test_second_active_run_returns_409(settings):
    planning = Message(
        role="assistant",
        tool_calls=[
            ToolCall(
                id="plan-1",
                name="update_plan",
                arguments={"steps": ["research", "report"], "current": 1},
            )
        ],
    )
    with make_client(settings, [planning, Message(role="assistant", content="done")]) as client:
        session = client.post("/api/sessions", json={}).json()
        first = client.post(f"/api/sessions/{session['id']}/runs", json={"question": "one"})
        assert first.status_code == 202
        second = client.post(f"/api/sessions/{session['id']}/runs", json={"question": "two"})
        assert second.status_code == 409
```

- [ ] **Step 3: Define Pydantic API models**

```python
# src/scout/web/schemas.py
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..approval import ApprovalAction


class CreateSessionRequest(BaseModel):
    title: str = ""


class CreateRunRequest(BaseModel):
    question: str = Field(min_length=1)


class CreateRunResponse(BaseModel):
    run_id: str
    session_id: str


class ApprovalSubmitRequest(BaseModel):
    action: ApprovalAction
    feedback: str = ""


class CancelRunResponse(BaseModel):
    run_id: str
    status: Literal["cancelling", "already_finished"]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    active_run_id: str | None = None


class SessionSummary(BaseModel):
    id: str
    title: str
    message_count: int
    created_at: float
    updated_at: float


class SessionDetail(BaseModel):
    id: str
    title: str
    messages: list[dict[str, Any]]
    plan: str
    plan_steps: list[str]
    plan_current: int
    sources: list[dict[str, Any]]
    usage: dict[str, int]
    active_run_id: str | None = None
    run_status: str = "idle"
```

- [ ] **Step 4: Implement SSE encoding and replay loop**

```python
# src/scout/web/sse.py
from __future__ import annotations

import json


def encode_sse(envelope) -> str:
    payload = json.dumps(envelope.to_dict(), ensure_ascii=False, default=str)
    return f"id: {envelope.id}\nevent: {envelope.type}\ndata: {payload}\n\n"


def stream_events(manager, run_id: str, after_id: int = 0):
    cursor = after_id
    while True:
        events = manager.events_after(run_id, cursor)
        for event in events:
            cursor = event.id
            yield encode_sse(event)
        record = manager.get(run_id)
        if record.status == "finished" and not manager.events_after(run_id, cursor):
            break
        if not manager.wait_for_events(run_id, cursor, timeout=15):
            yield ": keep-alive\n\n"
```

Add `RunManager.get(run_id)` and `active` accessors used by routes.

- [ ] **Step 5: Implement routes and application lifespan**

```python
# src/scout/web/app.py
from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import FastAPI

from ..config import Settings, load_settings
from ..runtime import Runtime
from .gateway import WebApprovalGateway
from .routes import build_router
from .run_manager import RunManager


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    workspace = Path(os.getenv("SCOUT_WORKSPACE", "."))
    settings = settings or load_settings(workspace)
    gateway = WebApprovalGateway()
    runtime = runtime or Runtime(settings, approval_gateway=gateway)
    if runtime.approval_gateway is None:
        runtime.approval_gateway = gateway
        runtime.approver.gateway = gateway
    manager = RunManager(runtime, gateway)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        app.state.gateway = gateway
        app.state.run_manager = manager
        yield
        manager.shutdown()
        runtime.close()

    app = FastAPI(title="Scout", lifespan=lifespan)
    app.include_router(build_router(), prefix="/api")
    return app
```

```python
# src/scout/web/routes.py
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..approval import ApprovalDecision
from .gateway import ApprovalAlreadyResolvedError, ApprovalNotFoundError
from .run_manager import ActiveRunError, RunNotFoundError
from .schemas import ApprovalSubmitRequest, CreateRunRequest, CreateSessionRequest
from .sse import stream_events


def build_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health(request: Request):
        active = request.app.state.run_manager.active
        return {"status": "ok", "active_run_id": active.run_id if active else None}

    @router.get("/sessions")
    def list_sessions(request: Request):
        return request.app.state.runtime.list_sessions()

    @router.post("/sessions", status_code=201)
    def create_session(body: CreateSessionRequest, request: Request):
        runtime = request.app.state.runtime
        session = runtime.new_session(body.title)
        return runtime.store.get_session(session.id)

    @router.get("/sessions/{session_id}")
    def get_session(session_id: str, request: Request):
        runtime = request.app.state.runtime
        manager = request.app.state.run_manager
        try:
            snapshot = runtime.session_snapshot(session_id)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        active = manager.active
        if active and active.session_id == session_id:
            snapshot["active_run_id"] = active.run_id
            snapshot["run_status"] = active.status
        else:
            snapshot["active_run_id"] = None
            snapshot["run_status"] = "idle"
        return snapshot

    @router.post("/sessions/{session_id}/runs", status_code=202)
    def start_run(session_id: str, body: CreateRunRequest, request: Request):
        if request.app.state.runtime.store.get_session(session_id) is None:
            raise HTTPException(404, f"找不到会话 {session_id}")
        try:
            record = request.app.state.run_manager.start_run(session_id, body.question)
        except ActiveRunError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"run_id": record.run_id, "session_id": record.session_id}

    @router.get("/runs/{run_id}/events")
    def run_events(
        run_id: str,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ):
        manager = request.app.state.run_manager
        try:
            manager.get(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(404, f"找不到运行 {run_id}") from exc
        after_id = int(last_event_id or 0)
        return StreamingResponse(
            stream_events(manager, run_id, after_id),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str, request: Request):
        try:
            status = request.app.state.run_manager.cancel(run_id)
        except RunNotFoundError as exc:
            raise HTTPException(404, f"找不到运行 {run_id}") from exc
        return {"run_id": run_id, "status": status}

    @router.post("/approvals/{approval_id}", status_code=202)
    def resolve_approval(approval_id: str, body: ApprovalSubmitRequest, request: Request):
        try:
            request.app.state.gateway.resolve(
                approval_id,
                ApprovalDecision(body.action, body.feedback),
            )
        except ApprovalNotFoundError as exc:
            raise HTTPException(404, f"找不到审批 {approval_id}") from exc
        except ApprovalAlreadyResolvedError as exc:
            raise HTTPException(409, f"审批 {approval_id} 已处理") from exc
        return {"approval_id": approval_id, "status": "accepted"}

    return router
```

Implement `RunManager.active` as a lock-protected property and `RunManager.get(run_id)` as a checked lookup returning `RunRecord`.

- [ ] **Step 6: Add `scout-web` entry point**

```python
# src/scout/web_cli.py
from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout-web")
    parser.add_argument("--workspace", "-w", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    os.environ["SCOUT_WORKSPACE"] = str(Path(args.workspace).resolve())
    uvicorn.run(
        "scout.web.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0
```

Add `scout-web = "scout.web_cli:main"` under `[project.scripts]`.

- [ ] **Step 7: Run API and Python regression**

Run: `uv sync --extra web && uv run pytest tests/test_web_api.py tests/test_run_manager.py tests/test_web_gateway.py -v && uv run pytest tests -q && uv run ruff check src tests`

Expected: API tests PASS, full Python suite PASS, Ruff exits 0.

- [ ] **Step 8: Commit if explicitly authorized**

```bash
git add pyproject.toml uv.lock src/scout/web src/scout/web_cli.py tests/test_web_api.py
git commit -m "$(cat <<'EOF'
feat: expose Scout runs over REST and SSE

EOF
)"
```

---

### Task 5: Scaffold React/Vite and lock the event reducer

**Files:**
- Create: `web/` Vite React TypeScript project.
- Create: `web/src/api/types.ts`
- Create: `web/src/state/runReducer.ts`
- Create: `web/src/state/runReducer.test.ts`
- Modify: `.gitignore`

**Interfaces:**
- Produces: TypeScript DTOs matching `schemas.py`.
- Produces: `runReducer(state, envelope) -> RunState`.

- [ ] **Step 1: Scaffold and install test dependencies**

Run:

```bash
npm create vite@latest web -- --template react-ts
cd web
npm install
npm install react-markdown
npm install --save-dev vitest jsdom @testing-library/react @testing-library/jest-dom
```

Expected: `web/package.json` and lockfile are created. Add scripts:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "test": "vitest run",
    "typecheck": "tsc -b --pretty false"
  }
}
```

- [ ] **Step 2: Write failing reducer tests**

```typescript
// web/src/state/runReducer.test.ts
import { describe, expect, it } from 'vitest'
import { initialRunState, runReducer } from './runReducer'

const event = (type: string, data: Record<string, unknown>) => ({
  id: 1,
  type,
  run_id: 'r1',
  session_id: 's1',
  ts: 1,
  agent: 'main',
  data,
})

describe('runReducer', () => {
  it('merges streaming deltas into one assistant message', () => {
    let state = runReducer(initialRunState, event('run_start', { input: 'question' }))
    state = runReducer(state, event('llm_delta', { text: 'hello ' }))
    state = runReducer(state, event('llm_delta', { text: 'world' }))
    expect(state.streamingText).toBe('hello world')
  })

  it('opens and resolves approval', () => {
    let state = runReducer(
      initialRunState,
      event('approval_required', { approval_id: 'a1', kind: 'plan', title: 'Plan', payload: {} }),
    )
    expect(state.approval?.approval_id).toBe('a1')
    state = runReducer(state, event('approval_resolved', { approval_id: 'a1', action: 'approve' }))
    expect(state.approval).toBeNull()
  })

  it('updates plan, tools, sources and completion status', () => {
    let state = runReducer(initialRunState, event('plan_updated', { plan: '[>] Search' }))
    state = runReducer(state, event('tool_start', { id: 't1', tool: 'web_search', arguments: {} }))
    state = runReducer(state, event('tool_end', { id: 't1', tool: 'web_search', ok: true }))
    state = runReducer(state, event('run_end', { stop_reason: 'completed' }))
    expect(state.plan).toContain('Search')
    expect(state.tools[0].ok).toBe(true)
    expect(state.status).toBe('idle')
  })
})
```

- [ ] **Step 3: Define shared API types**

```typescript
// web/src/api/types.ts
export type ApprovalAction = 'approve' | 'revise' | 'reject' | 'allow_session' | 'cancel'

export interface SSEEnvelope {
  id: number
  type: string
  run_id: string
  session_id: string
  ts: number
  agent: string
  data: Record<string, unknown>
}

export interface SessionSummary {
  id: string
  title: string
  message_count: number
  created_at: number
  updated_at: number
}

export interface SessionDetail {
  id: string
  title: string
  messages: Array<{ role: string; content: string }>
  plan: string
  plan_steps: string[]
  plan_current: number
  sources: Array<{ label: string; title: string; url: string; fetched_at: number }>
  usage: Record<string, number>
  active_run_id: string | null
  run_status: string
}
```

- [ ] **Step 4: Implement the reducer**

```typescript
// web/src/state/runReducer.ts
import type { SSEEnvelope } from '../api/types'

export interface ApprovalView {
  approval_id: string
  kind: string
  title: string
  payload: Record<string, unknown>
}

export interface RunState {
  status: 'idle' | 'running' | 'awaiting_approval' | 'cancelling'
  runId: string | null
  streamingText: string
  plan: string
  tools: Array<Record<string, unknown>>
  approval: ApprovalView | null
  error: string | null
}

export const initialRunState: RunState = {
  status: 'idle',
  runId: null,
  streamingText: '',
  plan: '',
  tools: [],
  approval: null,
  error: null,
}

export function runReducer(state: RunState, event: SSEEnvelope): RunState {
  switch (event.type) {
    case 'run_start':
      return { ...initialRunState, status: 'running', runId: event.run_id }
    case 'llm_delta':
      return { ...state, streamingText: state.streamingText + String(event.data.text ?? '') }
    case 'plan_updated':
      return { ...state, plan: String(event.data.plan ?? '') }
    case 'tool_start':
      return { ...state, tools: [...state.tools, { ...event.data, status: 'running' }] }
    case 'tool_end':
      return {
        ...state,
        tools: state.tools.map((tool) =>
          tool.tool === event.data.tool && tool.status === 'running'
            ? { ...tool, ...event.data, status: 'finished' }
            : tool,
        ),
      }
    case 'approval_required':
      return {
        ...state,
        status: 'awaiting_approval',
        approval: event.data as unknown as ApprovalView,
      }
    case 'approval_resolved':
      return { ...state, status: 'running', approval: null }
    case 'error':
      return { ...state, error: String(event.data.error ?? 'Unknown error') }
    case 'run_end':
      return { ...state, status: 'idle', runId: null, approval: null }
    default:
      return state
  }
}
```

- [ ] **Step 5: Run reducer tests and typecheck**

Run: `cd web && npm run test && npm run typecheck`

Expected: reducer tests PASS and TypeScript exits 0.

- [ ] **Step 6: Update ignore rules**

Add:

```gitignore
.superpowers/
web/node_modules/
web/test-results/
web/playwright-report/
```

Do not ignore `web/dist` because the wheel packaging task consumes it.

- [ ] **Step 7: Commit if explicitly authorized**

```bash
git add .gitignore web
git commit -m "$(cat <<'EOF'
feat: scaffold React workbench state

EOF
)"
```

---

### Task 6: Connect React to REST and SSE

**Files:**
- Create: `web/src/api/client.ts`
- Create: `web/src/api/sse.ts`
- Create: `web/src/state/useWorkbench.ts`
- Create: `web/src/api/client.test.ts`
- Create: `web/src/api/sse.test.ts`

**Interfaces:**
- Produces: typed REST functions.
- Produces: `subscribeToRun(runId, onEvent, onError)`.
- Produces: `useWorkbench()` controller for components.

- [ ] **Step 1: Write failing API URL and SSE parse tests**

```typescript
// web/src/api/sse.test.ts
import { describe, expect, it, vi } from 'vitest'
import { subscribeToRun } from './sse'

it('parses an SSE envelope and closes cleanly', () => {
  const received = vi.fn()
  const listeners: Record<string, (event: MessageEvent) => void> = {}
  const source = {
    onerror: null,
    addEventListener: vi.fn((name: string, handler: (event: MessageEvent) => void) => {
      listeners[name] = handler
    }),
    close: vi.fn(),
  }
  const close = subscribeToRun('r1', received, vi.fn(), () => source as never)
  listeners.run_start({ data: JSON.stringify({ id: 1, type: 'run_start' }) } as MessageEvent)
  expect(received).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }))
  close()
  expect(source.close).toHaveBeenCalled()
})
```

- [ ] **Step 2: Implement REST client**

```typescript
// web/src/api/client.ts
import type { ApprovalAction, SessionDetail, SessionSummary } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    const body = await response.text()
    throw new Error(body || `HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  sessions: () => request<SessionSummary[]>('/sessions'),
  session: (id: string) => request<SessionDetail>(`/sessions/${id}`),
  createSession: (title = '') =>
    request<SessionSummary>('/sessions', { method: 'POST', body: JSON.stringify({ title }) }),
  startRun: (sessionId: string, question: string) =>
    request<{ run_id: string; session_id: string }>(`/sessions/${sessionId}/runs`, {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),
  approve: (approvalId: string, action: ApprovalAction, feedback = '') =>
    request(`/approvals/${approvalId}`, {
      method: 'POST',
      body: JSON.stringify({ action, feedback }),
    }),
  cancel: (runId: string) => request(`/runs/${runId}/cancel`, { method: 'POST' }),
}
```

- [ ] **Step 3: Implement EventSource wrapper**

```typescript
// web/src/api/sse.ts
import type { SSEEnvelope } from './types'

const EVENT_TYPES = [
  'run_start', 'run_end', 'step_start', 'llm_start', 'llm_delta', 'llm_end',
  'tool_start', 'tool_end', 'memory_recall', 'compaction', 'plan_updated',
  'subagent_start', 'subagent_end', 'approval_required', 'approval_resolved', 'error',
]

export function subscribeToRun(
  runId: string,
  onEvent: (event: SSEEnvelope) => void,
  onError: (error: Event) => void,
  factory: (url: string) => EventSource = (url) => new EventSource(url),
): () => void {
  const source = factory(`/api/runs/${runId}/events`)
  const handle = (raw: Event) => {
    const message = raw as MessageEvent
    try {
      onEvent(JSON.parse(message.data) as SSEEnvelope)
    } catch {
      onError(new Event('parse-error'))
    }
  }
  for (const type of EVENT_TYPES) source.addEventListener(type, handle)
  source.onerror = onError
  return () => source.close()
}
```

- [ ] **Step 4: Implement workbench hook**

```typescript
// web/src/state/useWorkbench.ts
import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { api } from '../api/client'
import { subscribeToRun } from '../api/sse'
import type { ApprovalAction, SessionDetail, SessionSummary } from '../api/types'
import { initialRunState, runReducer } from './runReducer'

export function useWorkbench() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [run, dispatch] = useReducer(runReducer, initialRunState)
  const previousStatus = useRef(run.status)

  const refreshSessions = useCallback(async () => setSessions(await api.sessions()), [])
  const selectSession = useCallback(async (id: string) => {
    const detail = await api.session(id)
    setSession(detail)
    if (detail.active_run_id) {
      dispatch({
        id: 0, type: 'run_start', run_id: detail.active_run_id, session_id: detail.id,
        ts: Date.now() / 1000, agent: 'main', data: {},
      })
    }
  }, [])

  useEffect(() => {
    void refreshSessions()
  }, [refreshSessions])

  useEffect(() => {
    if (!run.runId) return
    return subscribeToRun(
      run.runId,
      dispatch,
      () => dispatch({
        id: -1, type: 'error', run_id: run.runId ?? '', session_id: session?.id ?? '',
        ts: Date.now() / 1000, agent: 'main', data: { error: '事件流连接中断' },
      }),
    )
  }, [run.runId, session?.id])

  useEffect(() => {
    const wasRunning = previousStatus.current !== 'idle'
    if (wasRunning && run.status === 'idle' && session) {
      void api.session(session.id).then(setSession)
      void refreshSessions()
    }
    previousStatus.current = run.status
  }, [run.status, session?.id, refreshSessions])

  const start = async (question: string) => {
    const current = session ?? (await api.createSession())
    if (!session) setSession(await api.session(current.id))
    const created = await api.startRun(current.id, question)
    dispatch({
      id: 0, type: 'run_start', run_id: created.run_id, session_id: current.id,
      ts: Date.now() / 1000, agent: 'main', data: { input: question },
    })
  }

  const decide = (action: ApprovalAction, feedback = '') =>
    run.approval ? api.approve(run.approval.approval_id, action, feedback) : Promise.resolve()

  return { sessions, session, run, refreshSessions, selectSession, start, decide, api }
}
```

- [ ] **Step 5: Run frontend unit tests**

Run: `cd web && npm run test && npm run typecheck`

Expected: API/SSE tests PASS and TypeScript exits 0.

- [ ] **Step 6: Commit if explicitly authorized**

```bash
git add web/src/api web/src/state
git commit -m "$(cat <<'EOF'
feat: connect workbench to Scout API

EOF
)"
```

---

### Task 7: Build the three-column workbench UI

**Files:**
- Create: `web/src/components/TopBar.tsx`
- Create: `web/src/components/SessionList.tsx`
- Create: `web/src/components/PlanPanel.tsx`
- Create: `web/src/components/ChatPanel.tsx`
- Create: `web/src/components/EventPanel.tsx`
- Create: `web/src/components/SourcesPanel.tsx`
- Create: `web/src/components/ApprovalModal.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/main.tsx`
- Create: `web/src/styles.css`
- Create: `web/src/components/ApprovalModal.test.tsx`

**Interfaces:**
- Consumes: `useWorkbench`.
- Produces: approved workbench layout and all user actions.

- [ ] **Step 1: Write failing approval modal interaction tests**

```typescript
// web/src/components/ApprovalModal.test.tsx
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { ApprovalModal } from './ApprovalModal'

it('submits plan revision feedback', () => {
  const onDecision = vi.fn()
  render(
    <ApprovalModal
      approval={{ approval_id: 'a1', kind: 'plan', title: 'Plan', payload: { plan: 'Search' } }}
      onDecision={onDecision}
    />,
  )
  fireEvent.change(screen.getByLabelText('修改意见'), { target: { value: 'Add validation' } })
  fireEvent.click(screen.getByRole('button', { name: '要求修改' }))
  expect(onDecision).toHaveBeenCalledWith('revise', 'Add validation')
})
```

- [ ] **Step 2: Implement ApprovalModal**

```tsx
// web/src/components/ApprovalModal.tsx
import { useState } from 'react'
import type { ApprovalAction } from '../api/types'
import type { ApprovalView } from '../state/runReducer'

export function ApprovalModal({
  approval,
  onDecision,
}: {
  approval: ApprovalView
  onDecision: (action: ApprovalAction, feedback?: string) => void
}) {
  const [feedback, setFeedback] = useState('')
  const isPlan = approval.kind === 'plan'
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="approval-modal" role="dialog" aria-modal="true">
        <h2>{approval.title}</h2>
        <pre>{String(approval.payload.plan ?? approval.payload.tool ?? '')}</pre>
        {isPlan && (
          <label>
            修改意见
            <textarea value={feedback} onChange={(event) => setFeedback(event.target.value)} />
          </label>
        )}
        <div className="actions">
          <button onClick={() => onDecision('approve')}>允许一次</button>
          {isPlan ? (
            <button onClick={() => onDecision('revise', feedback)}>要求修改</button>
          ) : (
            <>
              <button onClick={() => onDecision('allow_session')}>本会话允许</button>
              <button onClick={() => onDecision('reject', feedback)}>拒绝</button>
            </>
          )}
          <button className="danger" onClick={() => onDecision('cancel')}>取消运行</button>
        </div>
      </section>
    </div>
  )
}
```

- [ ] **Step 3: Implement focused presentation components**

```tsx
// web/src/components/SessionList.tsx
import type { SessionSummary } from '../api/types'

export function SessionList({
  sessions, selectedId, onSelect, onNew,
}: {
  sessions: SessionSummary[]
  selectedId: string | null
  onSelect: (id: string) => void
  onNew: () => void
}) {
  return (
    <section>
      <div className="section-heading">
        <h2>会话</h2><button onClick={onNew}>新建会话</button>
      </div>
      <ul className="session-list">
        {sessions.map((session) => (
          <li key={session.id}>
            <button
              className={session.id === selectedId ? 'selected' : ''}
              onClick={() => onSelect(session.id)}
            >
              {session.title || '未命名调研'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
```

```tsx
// web/src/components/PlanPanel.tsx
export function PlanPanel({ plan }: { plan: string }) {
  return <section><h2>计划</h2><pre className="plan">{plan || '尚未制定计划'}</pre></section>
}
```

```tsx
// web/src/components/ChatPanel.tsx
import { FormEvent, useState } from 'react'
import ReactMarkdown from 'react-markdown'

export function ChatPanel({
  messages, streamingText, disabled, onSubmit,
}: {
  messages: Array<{ role: string; content: string }>
  streamingText: string
  disabled: boolean
  onSubmit: (question: string) => Promise<void>
}) {
  const [question, setQuestion] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const value = question.trim()
    if (!value || disabled) return
    setQuestion('')
    await onSubmit(value)
  }
  return (
    <section className="chat-column">
      <div className="messages">
        {messages.filter((message) => ['user', 'assistant'].includes(message.role)).map((message, index) => (
          <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
            <ReactMarkdown>{message.content}</ReactMarkdown>
          </article>
        ))}
        {streamingText && <article className="message assistant"><ReactMarkdown>{streamingText}</ReactMarkdown></article>}
      </div>
      <form onSubmit={submit}>
        <textarea
          placeholder="输入研究问题"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          disabled={disabled}
        />
        <button disabled={disabled || !question.trim()}>开始调研</button>
      </form>
    </section>
  )
}
```

```tsx
// web/src/components/EventPanel.tsx
export function EventPanel({
  tools, status,
}: {
  tools: Array<Record<string, unknown>>
  status: string
}) {
  return (
    <section>
      <h2>实时活动 · {status}</h2>
      <ol className="event-list">
        {tools.map((tool, index) => (
          <li key={`${String(tool.tool)}-${index}`}>
            <strong>{String(tool.tool ?? 'tool')}</strong>
            <span>{String(tool.status ?? '')}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}
```

```tsx
// web/src/components/SourcesPanel.tsx
type Source = { label: string; title: string; url: string; fetched_at: number }

export function SourcesPanel({ sources }: { sources: Source[] }) {
  return (
    <section>
      <h2>来源</h2>
      <ul className="source-list">
        {sources.map((source) => (
          <li key={source.label}>
            <a href={source.url} target="_blank" rel="noreferrer">
              [{source.label}] {source.title || source.url}
            </a>
          </li>
        ))}
      </ul>
    </section>
  )
}
```

```tsx
// web/src/components/TopBar.tsx
export function TopBar({
  title, status, onCancel,
}: {
  title: string
  status: string
  onCancel: () => void
}) {
  return (
    <header className="top-bar">
      <strong>Scout · {title}</strong>
      <div><span className={`status ${status}`}>{status}</span>
        {status !== 'idle' && <button className="danger" onClick={onCancel}>停止运行</button>}
      </div>
    </header>
  )
}
```

Keep network and reducer logic out of these presentation components.

- [ ] **Step 4: Assemble App**

```tsx
// web/src/App.tsx
import { ApprovalModal } from './components/ApprovalModal'
import { ChatPanel } from './components/ChatPanel'
import { EventPanel } from './components/EventPanel'
import { PlanPanel } from './components/PlanPanel'
import { SessionList } from './components/SessionList'
import { SourcesPanel } from './components/SourcesPanel'
import { TopBar } from './components/TopBar'
import { useWorkbench } from './state/useWorkbench'
import './styles.css'

export default function App() {
  const workbench = useWorkbench()
  return (
    <main className="app-shell">
      <TopBar
        title={workbench.session?.title ?? 'Scout'}
        status={workbench.run.status}
        onCancel={() => workbench.run.runId && workbench.api.cancel(workbench.run.runId)}
      />
      <div className="workbench">
        <aside className="left-column">
          <SessionList
            sessions={workbench.sessions}
            selectedId={workbench.session?.id ?? null}
            onSelect={workbench.selectSession}
            onNew={() => workbench.api.createSession().then((s) => workbench.selectSession(s.id))}
          />
          <PlanPanel plan={workbench.run.plan || workbench.session?.plan || ''} />
        </aside>
        <ChatPanel
          messages={workbench.session?.messages ?? []}
          streamingText={workbench.run.streamingText}
          disabled={workbench.run.status !== 'idle'}
          onSubmit={workbench.start}
        />
        <aside className="right-column">
          <EventPanel tools={workbench.run.tools} status={workbench.run.status} />
          <SourcesPanel sources={workbench.session?.sources ?? []} />
        </aside>
      </div>
      {workbench.run.approval && (
        <ApprovalModal approval={workbench.run.approval} onDecision={workbench.decide} />
      )}
    </main>
  )
}
```

- [ ] **Step 5: Add responsive visual styling**

```css
/* web/src/styles.css */
:root { color: #172033; background: #f4f7fb; font-family: Inter, system-ui, sans-serif; }
* { box-sizing: border-box; }
body { margin: 0; }
button, textarea { font: inherit; }
button { cursor: pointer; }
.app-shell { min-height: 100vh; }
.top-bar {
  height: 58px; padding: 0 20px; display: flex; align-items: center;
  justify-content: space-between; color: white; background: #172554;
}
.top-bar > div { display: flex; align-items: center; gap: 12px; }
.status { padding: 4px 9px; border-radius: 999px; background: #334155; }
.status.running { background: #0369a1; }
.status.awaiting_approval { background: #b45309; }
.workbench {
  height: calc(100vh - 58px); display: grid;
  grid-template-columns: 260px minmax(0, 1fr) 320px; gap: 1px; background: #dbe2ea;
}
.left-column, .right-column, .chat-column { min-width: 0; overflow: auto; background: white; padding: 16px; }
.section-heading { display: flex; align-items: center; justify-content: space-between; }
.session-list, .source-list, .event-list { list-style: none; margin: 0; padding: 0; }
.session-list button { width: 100%; padding: 9px; border: 0; text-align: left; background: transparent; }
.session-list button.selected { color: #4338ca; background: #eef2ff; border-radius: 8px; }
.plan { white-space: pre-wrap; color: #475569; }
.chat-column { display: flex; flex-direction: column; }
.messages { flex: 1; overflow: auto; }
.message { max-width: 85%; margin: 10px 0; padding: 12px 14px; border-radius: 12px; background: #f1f5f9; }
.message.user { margin-left: auto; background: #e0e7ff; }
.chat-column form { display: grid; grid-template-columns: 1fr auto; gap: 10px; }
.chat-column textarea { min-height: 72px; resize: vertical; padding: 10px; }
.source-list a { display: block; padding: 7px 0; color: #4338ca; text-decoration: none; }
.event-list li { display: flex; justify-content: space-between; padding: 7px 0; }
.modal-backdrop { position: fixed; inset: 0; display: grid; place-items: center; background: #0f172a99; }
.approval-modal { width: min(620px, 92vw); padding: 22px; border-radius: 14px; background: white; }
.approval-modal textarea { width: 100%; min-height: 90px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.danger { color: white; border: 0; border-radius: 7px; padding: 7px 11px; background: #b91c1c; }
button:focus-visible, textarea:focus-visible, a:focus-visible { outline: 3px solid #818cf8; outline-offset: 2px; }
@media (max-width: 900px) {
  .workbench { height: auto; grid-template-columns: 1fr; }
  .chat-column { min-height: 70vh; order: -1; }
}
```

Do not add a component library in this version.

- [ ] **Step 6: Run component tests and build**

Run: `cd web && npm run test && npm run typecheck && npm run build`

Expected: tests PASS, typecheck exits 0, Vite creates `web/dist`.

- [ ] **Step 7: Commit if explicitly authorized**

```bash
git add web/src web/dist
git commit -m "$(cat <<'EOF'
feat: add Scout research workbench UI

EOF
)"
```

---

### Task 8: Package the SPA, add E2E coverage, and document launch

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/e2e/workbench.spec.ts`
- Create: `web/e2e/server.py`
- Modify: `web/package.json`
- Modify: `src/scout/web/app.py`
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `docs/design.md`
- Modify: `docs/flows.md`

**Interfaces:**
- `scout-web` serves `/api/*` and the built SPA.
- Wheel includes `web/dist` as `scout/web/static`.

- [ ] **Step 1: Add Playwright E2E tooling**

Run:

```bash
cd web
npm install --save-dev @playwright/test
npx playwright install chromium
```

Add `"e2e": "playwright test"` to package scripts.

- [ ] **Step 2: Write the complete workbench E2E**

```typescript
// web/e2e/workbench.spec.ts
import { expect, test } from '@playwright/test'

test('confirms a plan, approves a tool, and renders cited output', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: '新建会话' }).click()
  await page.getByPlaceholder('输入研究问题').fill('Compare SQLite and DuckDB')
  await page.getByRole('button', { name: '开始调研' }).click()

  await expect(page.getByRole('dialog')).toContainText('调研计划待确认')
  await page.getByRole('button', { name: '允许一次' }).click()

  await expect(page.getByRole('dialog')).toContainText('http_request')
  await page.getByRole('button', { name: '本会话允许' }).click()

  await expect(page.getByText('[S1]')).toBeVisible()
  await expect(page.locator('.status')).toHaveText('idle')
})
```

```python
# web/e2e/server.py
from pathlib import Path

from scout.config import Settings
from scout.llm.base import LLMResponse, Message, ToolCall, Usage
from scout.runtime import Runtime
from scout.web.app import create_app


class E2ELLM:
    model = "e2e"
    embedding_model = ""

    def __init__(self) -> None:
        self.script = [
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="plan-1",
                        name="update_plan",
                        arguments={"steps": ["research", "report"], "current": 1},
                    )
                ],
            ),
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="tool-1",
                        name="http_request",
                        arguments={"url": "http://127.0.0.1:9/unreachable"},
                    )
                ],
            ),
            Message(role="assistant", content="completed research report [S1]"),
        ]

    def chat(self, messages, tools=None, *, stream=False, on_delta=None, **kwargs):
        message = self.script.pop(0)
        if stream and on_delta and message.content:
            on_delta(message.content)
        return LLMResponse(message=message, usage=Usage(prompt_tokens=10, completion_tokens=5, calls=1))

    def embed(self, texts):
        return []


workspace = Path(".e2e-workspace").resolve()
home = workspace / ".scout"
db = home / "scout.db"
if db.exists():
    db.unlink()
settings = Settings(
    api_key="e2e",
    model="e2e",
    workspace=workspace,
    home=home,
    permission_mode="ask",
    max_steps=6,
)
settings.ensure_dirs()
runtime = Runtime(settings, llm=E2ELLM(), enable_trace=False)
app = create_app(settings=settings, runtime=runtime)
```

```typescript
// web/playwright.config.ts
import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  use: { baseURL: 'http://127.0.0.1:5173' },
  webServer: [
    {
      command: 'uv run --project .. --extra web uvicorn server:app --app-dir e2e --host 127.0.0.1 --port 8000',
      port: 8000,
      reuseExistingServer: false,
    },
    {
      command: 'npm run dev -- --host 127.0.0.1 --port 5173',
      port: 5173,
      reuseExistingServer: false,
    },
  ],
})
```

This deterministic app exercises the real Agent, approval gateway, REST, and SSE code without a real model or public-network dependency.

- [ ] **Step 3: Serve built frontend and include it in wheels**

After API routes in `create_app`, mount the built static directory:

```python
from pathlib import Path
from fastapi.staticfiles import StaticFiles

packaged_static = Path(__file__).with_name("static")
source_static = Path(__file__).parents[3] / "web" / "dist"
static_dir = packaged_static if packaged_static.exists() else source_static
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="frontend")
```

Configure Vite:

```typescript
// web/vite.config.ts
export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  build: { outDir: 'dist', emptyOutDir: true },
})
```

Configure Hatch:

```toml
[tool.hatch.build.targets.wheel.force-include]
"web/dist" = "scout/web/static"
```

API routes must be registered before the catch-all static mount.

- [ ] **Step 4: Update user and architecture documentation**

Add to README:

```bash
uv sync --extra web
cd web && npm install && npm run build && cd ..
uv run scout-web --workspace . --host 127.0.0.1 --port 8080
```

Add the approved architecture, plan/tool approval flow, SSE flow, trafilatura/Playwright pipeline, and arXiv URL behavior to `docs/design.md` and `docs/flows.md`. Preserve the user's existing uncommitted edits and append or merge without overwriting them.

- [ ] **Step 5: Run full verification**

Run:

```bash
uv sync --extra web
uv run pytest tests -q
uv run ruff check src tests
cd web
npm run test
npm run typecheck
npm run build
npm run e2e
cd ..
uv build
```

Expected:

- Python suite PASS;
- Ruff exits 0;
- Vitest and Playwright PASS;
- TypeScript and Vite build PASS;
- wheel build PASS and contains `scout/web/static/index.html`.

- [ ] **Step 6: Manual local smoke**

Run: `uv run scout-web --workspace . --host 127.0.0.1 --port 8080`

Verify:

- `http://127.0.0.1:8080/api/health` returns `{"status":"ok",...}`;
- the workbench creates/resumes sessions;
- the first plan pauses before search;
- risky tools wait for the modal;
- Stop ends with `cancelled`;
- sources and cited report appear;
- refreshing during a run restores state and reconnects SSE.

- [ ] **Step 7: Commit if explicitly authorized**

```bash
git add pyproject.toml README.md docs/design.md docs/flows.md src/scout/web web
git commit -m "$(cat <<'EOF'
feat: deliver local Scout web workbench

EOF
)"
```

## Plan Completion Check

- Run the full verification block in Task 8.
- Confirm no API key, request headers, or full page content appears in errors/SSE.
- Confirm API returns 404 for unknown approval/run and 409 for duplicate approval/active run.
- Confirm SSE reconnect does not duplicate event IDs.
- Confirm page refresh restores persisted plan, messages, sources, and usage.
- Confirm CLI ask/auto/readonly regression remains green.
- Confirm the wheel contains the built React files.
