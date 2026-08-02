import threading
import time

import pytest
from conftest import FakeLLM, assistant_tool_call

from scout.approval import ApprovalAction, ApprovalKind, ApprovalRequest
from scout.core.events import EventBus, EventType
from scout.llm.base import Message
from scout.runtime import Runtime
from scout.web.gateway import WebApprovalGateway
from scout.web.run_manager import (
    ActiveRunError,
    RunManager,
    RunManagerClosedError,
    RunNotFoundError,
)
from scout.web.sse import stream_events


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
        self.close_calls = 0
        self.closed = threading.Event()

    def resume_session(self, session_id):
        return type("Session", (), {"id": session_id})()

    def build_agent(self, session, cancellation=None):
        return BlockingAgent(self.bus, self.release)

    def close(self):
        self.close_calls += 1
        self.closed.set()


def wait_for_thread(record):
    assert record.thread is not None
    record.thread.join(2)
    assert record.thread.is_alive() is False


def test_buffers_immediate_events_with_monotonic_ids_and_rejects_second_run():
    runtime = FakeRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")

    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)
    assert manager.wait_for_events(record.run_id, after_id=1, timeout=1)
    events = manager.events_after(record.run_id, 0)
    assert [event.id for event in events] == list(range(1, len(events) + 1))
    assert [event.type for event in events] == ["run_start", "llm_delta"]
    assert all(event.run_id == record.run_id for event in events)
    assert all(event.session_id == "s1" for event in events)
    with pytest.raises(ActiveRunError):
        manager.start_run("s2", "second")

    runtime.release.set()
    wait_for_thread(record)


def test_replays_only_events_newer_than_requested_id_after_completion():
    runtime = FakeRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)

    runtime.release.set()
    wait_for_thread(record)

    events = manager.events_after(record.run_id, 1)
    assert [event.id for event in events] == [2, 3]
    assert record.status == "finished"
    assert manager.wait_for_events(record.run_id, after_id=3, timeout=0) is True


def test_real_worker_lifecycle_does_not_terminate_lead_event_stream(settings):
    llm = FakeLLM(
        [
            assistant_tool_call(
                "research_subtopic",
                {"topic": "worker lifecycle"},
                call_id="delegate",
            ),
            Message(role="assistant", content="worker result"),
            assistant_tool_call("list_dir", {"path": "."}, call_id="after-worker"),
            Message(role="assistant", content="lead final"),
        ]
    )
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    manager = RunManager(runtime)

    try:
        record = manager.start_run(session.id, "research")
        wait_for_thread(record)
        events = manager.events_after(record.run_id, 0)
        frames = list(stream_events(manager, record.run_id))
    finally:
        manager.shutdown()

    lifecycle = [
        (event.type, event.agent)
        for event in events
        if event.type in {"run_start", "run_end"}
    ]
    assert lifecycle == [("run_start", "main"), ("run_end", "main")]
    assert events[-1].type == "run_end"
    subagent_end_index = next(
        index for index, event in enumerate(events) if event.type == "subagent_end"
    )
    later_tool_index = next(
        index
        for index, event in enumerate(events)
        if event.type == "tool_start" and event.data.get("id") == "after-worker"
    )
    assert subagent_end_index < later_tool_index < len(events) - 1
    worker_name = next(
        event.data["worker"] for event in events if event.type == "subagent_start"
    )
    assert any(event.type == "llm_end" and event.agent == worker_name for event in events)
    assert sum("\nevent: run_end\n" in frame for frame in frames) == 1
    assert "\nevent: run_end\n" in frames[-1]


def test_run_manager_ignores_defensive_worker_run_lifecycle_events():
    runtime = FakeRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)

    runtime.bus.emit(EventType.RUN_START, {"run_id": record.run_id}, agent="worker-0")
    runtime.bus.emit(EventType.RUN_END, {"run_id": record.run_id}, agent="worker-0")

    assert all(
        event.agent == "main"
        for event in manager.events_after(record.run_id, 0)
        if event.type in {"run_start", "run_end"}
    )
    assert record.run_end_emitted is False

    runtime.release.set()
    wait_for_thread(record)
    assert record.run_end_emitted is True


def test_cancel_requests_token_and_notifies_approval_gateway():
    class ApprovalGateway:
        def __init__(self):
            self.cancelled_runs = []

        def cancel_run(self, run_id):
            self.cancelled_runs.append(run_id)

    runtime = FakeRuntime()
    gateway = ApprovalGateway()
    manager = RunManager(runtime, approval_gateway=gateway)
    record = manager.start_run("s1", "question")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)

    assert manager.cancel(record.run_id) == "cancelling"
    assert record.cancellation.is_cancelled() is True
    assert record.status == "cancelling"
    assert gateway.cancelled_runs == [record.run_id]

    runtime.bus.emit(EventType.APPROVAL_REQUIRED, {})
    assert record.status == "cancelling"

    runtime.release.set()
    wait_for_thread(record)
    assert manager.cancel(record.run_id) == "already_finished"


def test_approval_events_transition_status_unless_cancelling():
    runtime = FakeRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)

    runtime.bus.emit(EventType.APPROVAL_REQUIRED, {"approval_id": "a1"})
    assert record.status == "awaiting_approval"
    runtime.bus.emit(EventType.APPROVAL_RESOLVED, {"approval_id": "a1", "action": "approve"})
    assert record.status == "running"

    manager.cancel(record.run_id)
    runtime.bus.emit(EventType.APPROVAL_REQUIRED, {"approval_id": "a2"})
    runtime.bus.emit(EventType.APPROVAL_RESOLVED, {"approval_id": "a2", "action": "cancel"})
    assert record.status == "cancelling"

    runtime.release.set()
    wait_for_thread(record)


def test_shutdown_cancels_and_joins_the_active_run():
    class CancellableAgent:
        def __init__(self, bus, cancellation):
            self.bus = bus
            self.cancellation = cancellation

        def run(self, question, stream=True, run_id=None, **kwargs):
            self.bus.emit(EventType.RUN_START, {"run_id": run_id, "input": question})
            while not self.cancellation.is_cancelled():
                threading.Event().wait(0.01)
            self.bus.emit(EventType.RUN_END, {"run_id": run_id, "stop_reason": "cancelled"})

    class CancellableRuntime(FakeRuntime):
        def build_agent(self, session, cancellation=None):
            return CancellableAgent(self.bus, cancellation)

    runtime = CancellableRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)

    manager.shutdown()

    assert record.cancellation.is_cancelled() is True
    assert record.status == "finished"
    assert record.thread is not None
    assert record.thread.is_alive() is False
    assert runtime.close_calls == 1


def test_shutdown_rejects_successor_after_active_run_completes():
    class BlockingGateway:
        def __init__(self):
            self.cancel_called = threading.Event()
            self.release = threading.Event()

        def cancel_run(self, run_id):
            self.cancel_called.set()
            self.release.wait(1)

    class CancellableAgent:
        def __init__(self, bus, cancellation):
            self.bus = bus
            self.cancellation = cancellation

        def run(self, question, stream=True, run_id=None, **kwargs):
            self.bus.emit(EventType.RUN_START, {"run_id": run_id, "input": question})
            while not self.cancellation.is_cancelled():
                threading.Event().wait(0.01)
            self.bus.emit(EventType.RUN_END, {"run_id": run_id, "stop_reason": "cancelled"})

    class CancellableRuntime(FakeRuntime):
        def build_agent(self, session, cancellation=None):
            return CancellableAgent(self.bus, cancellation)

    runtime = CancellableRuntime()
    gateway = BlockingGateway()
    manager = RunManager(runtime, approval_gateway=gateway)
    record = manager.start_run("s1", "question")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)

    shutdown_thread = threading.Thread(target=manager.shutdown)
    shutdown_thread.start()
    assert gateway.cancel_called.wait(1)
    wait_for_thread(record)

    with pytest.raises(RunManagerClosedError, match="关闭"):
        manager.start_run("s2", "successor")

    gateway.release.set()
    shutdown_thread.join(1)
    assert shutdown_thread.is_alive() is False


@pytest.mark.parametrize("failure_stage", ["resume_session", "build_agent", "agent_run"])
def test_escaping_run_failures_append_sanitized_terminal_events(failure_stage):
    secret = "api-key=do-not-expose"

    class RaisingAgent:
        def run(self, question, stream=True, run_id=None, **kwargs):
            raise RuntimeError(secret)

    class FailingRuntime(FakeRuntime):
        def resume_session(self, session_id):
            if failure_stage == "resume_session":
                raise RuntimeError(secret)
            return super().resume_session(session_id)

        def build_agent(self, session, cancellation=None):
            if failure_stage == "build_agent":
                raise RuntimeError(secret)
            return RaisingAgent()

    runtime = FailingRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")
    wait_for_thread(record)

    events = manager.events_after(record.run_id, 0)
    assert [event.type for event in events] == ["error", "run_end"]
    assert [event.id for event in events] == [1, 2]
    assert all(event.run_id == record.run_id for event in events)
    assert all(event.session_id == "s1" for event in events)
    assert events[0].data == {
        "error": "run_failed",
        "code": f"{failure_stage}_failed",
        "type": "RuntimeError",
    }
    assert events[1].data == {
        "run_id": record.run_id,
        "stop_reason": "error",
        "code": f"{failure_stage}_failed",
    }
    assert secret not in repr([event.to_dict() for event in events])
    assert manager.events_after(record.run_id, 0) == events


def test_escaping_run_failure_does_not_duplicate_existing_terminal_events():
    class TerminalThenRaisingAgent:
        def __init__(self, bus):
            self.bus = bus

        def run(self, question, stream=True, run_id=None, **kwargs):
            self.bus.emit(
                EventType.ERROR,
                {"error": "run_failed", "code": "agent_run_failed", "type": "RuntimeError"},
            )
            self.bus.emit(
                EventType.RUN_END,
                {"run_id": run_id, "stop_reason": "error", "code": "agent_run_failed"},
            )
            raise RuntimeError("secret")

    class FailingRuntime(FakeRuntime):
        def build_agent(self, session, cancellation=None):
            return TerminalThenRaisingAgent(self.bus)

    runtime = FailingRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")
    wait_for_thread(record)

    event_types = [event.type for event in manager.events_after(record.run_id, 0)]
    assert event_types.count("error") == 1
    assert event_types.count("run_end") == 1


def test_run_finished_cleanup_happens_after_last_possible_request():
    class RecordingGateway:
        def __init__(self):
            self.finished = []

        def run_finished(self, run_id):
            self.finished.append(run_id)

    runtime = FakeRuntime()
    gateway = RecordingGateway()
    manager = RunManager(runtime, approval_gateway=gateway)
    record = manager.start_run("s1", "question")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)
    assert gateway.finished == []

    runtime.release.set()
    wait_for_thread(record)

    assert gateway.finished == [record.run_id]


def test_cancel_before_delayed_approval_registration_never_exposes_modal():
    class DelayedApprovalAgent:
        def __init__(self, runtime):
            self.runtime = runtime

        def run(self, question, stream=True, run_id=None, **kwargs):
            self.runtime.ready.set()
            self.runtime.release_approval.wait()
            request = ApprovalRequest.create(
                run_id,
                "s1",
                ApprovalKind.TOOL,
                "Tool",
                {},
            )
            self.runtime.decision = self.runtime.gateway.request(
                request,
                lambda kind, data: self.runtime.bus.emit(kind, data),
            )

    class DelayedApprovalRuntime(FakeRuntime):
        def __init__(self, gateway):
            super().__init__()
            self.gateway = gateway
            self.ready = threading.Event()
            self.release_approval = threading.Event()
            self.decision = None

        def build_agent(self, session, cancellation=None):
            return DelayedApprovalAgent(self)

    gateway = WebApprovalGateway()
    runtime = DelayedApprovalRuntime(gateway)
    manager = RunManager(runtime, approval_gateway=gateway)
    record = manager.start_run("s1", "question")
    assert runtime.ready.wait(1)

    assert manager.cancel(record.run_id) == "cancelling"
    runtime.release_approval.set()
    wait_for_thread(record)

    assert runtime.decision.action is ApprovalAction.CANCEL
    assert "approval_required" not in [
        event.type for event in manager.events_after(record.run_id, 0)
    ]


def test_slow_shutdown_defers_runtime_close_until_run_thread_finishes():
    class SlowAgent:
        def __init__(self, runtime):
            self.runtime = runtime

        def run(self, question, stream=True, run_id=None, **kwargs):
            self.runtime.bus.emit(EventType.RUN_START, {"run_id": run_id, "input": question})
            self.runtime.release.wait()
            assert self.runtime.closed.is_set() is False
            self.runtime.order.append("run_finished")
            self.runtime.bus.emit(EventType.RUN_END, {"run_id": run_id, "stop_reason": "cancelled"})

    class SlowRuntime(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.order = []

        def build_agent(self, session, cancellation=None):
            return SlowAgent(self)

        def close(self):
            self.order.append("runtime_closed")
            super().close()

    runtime = SlowRuntime()
    manager = RunManager(runtime)
    manager._SHUTDOWN_GRACE_SECONDS = 0.01
    record = manager.start_run("s1", "question")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)

    started = time.monotonic()
    manager.shutdown()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert record.thread is not None and record.thread.is_alive()
    assert runtime.close_calls == 0

    runtime.release.set()
    assert runtime.closed.wait(1)
    wait_for_thread(record)
    manager.shutdown()

    assert runtime.order == ["run_finished", "runtime_closed"]
    assert runtime.close_calls == 1


def test_failed_thread_start_rolls_back_reservation(monkeypatch):
    runtime = FakeRuntime()
    manager = RunManager(runtime)
    original_start = threading.Thread.start
    calls = 0

    def fail_once(thread):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("thread start failed")
        original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_once)

    with pytest.raises(RuntimeError, match="thread start failed"):
        manager.start_run("s1", "question")

    assert manager._active is None
    assert manager._records == {}

    record = manager.start_run("s2", "retry")
    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)
    runtime.release.set()
    wait_for_thread(record)


def test_finished_records_remain_replayable_until_oldest_is_evicted():
    runtime = FakeRuntime()
    manager = RunManager(runtime)
    records = []

    for index in range(21):
        record = manager.start_run(f"s{index}", "question")
        assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)
        runtime.release.set()
        wait_for_thread(record)
        runtime.release = threading.Event()
        records.append(record)

    assert [event.id for event in manager.events_after(records[-1].run_id, 0)] == [1, 2, 3]
    with pytest.raises(RunNotFoundError):
        manager.events_after(records[0].run_id, 0)
