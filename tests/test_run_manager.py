import threading

import pytest

from scout.core.events import EventBus, EventType
from scout.web.run_manager import ActiveRunError, RunManager, RunNotFoundError


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


def wait_for_thread(record):
    assert record.thread is not None
    record.thread.join(2)
    assert record.thread.is_alive() is False


def test_buffers_immediate_events_with_monotonic_ids_and_rejects_second_run():
    runtime = FakeRuntime()
    manager = RunManager(runtime)
    record = manager.start_run("s1", "question")

    assert manager.wait_for_events(record.run_id, after_id=0, timeout=1)
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
