"""Single-user background run management for the web workbench."""

from __future__ import annotations

import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Literal

from ..cancellation import RunCancellation
from ..core.events import Event, EventType


class ActiveRunError(RuntimeError):
    """Raised when a second lead run is started while one is active."""


class RunManagerClosedError(ActiveRunError):
    """Raised when a run is requested after manager shutdown begins."""


class RunNotFoundError(KeyError):
    """Raised when a run is no longer retained."""


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


class RunManager:
    """Run one lead Agent at a time and retain its recent events for replay."""

    _MAX_RECORDS = 20

    def __init__(self, runtime: Any, approval_gateway: Any = None) -> None:
        self.runtime = runtime
        self.approval_gateway = approval_gateway
        self._lock = threading.RLock()
        self._active: RunRecord | None = None
        self._records: OrderedDict[str, RunRecord] = OrderedDict()
        self._shutting_down = False
        runtime.bus.subscribe(self.on_event)

    def start_run(self, session_id: str, question: str) -> RunRecord:
        with self._lock:
            if self._shutting_down:
                raise RunManagerClosedError("运行管理器正在关闭，不能启动新任务")
            if self._active is not None:
                raise ActiveRunError("已有任务正在运行")

            record = RunRecord(
                run_id=uuid.uuid4().hex[:12],
                session_id=session_id,
                cancellation=RunCancellation(),
            )
            self._active = record
            self._records[record.run_id] = record
            record.thread = threading.Thread(
                target=self._run,
                args=(record, question),
                name=f"scout-run-{record.run_id}",
                daemon=True,
            )
            try:
                record.thread.start()
            except Exception:
                if self._active is record:
                    self._active = None
                self._records.pop(record.run_id, None)
                raise
            self._evict_old_records()
            return record

    def _evict_old_records(self) -> None:
        while len(self._records) > self._MAX_RECORDS:
            self._records.popitem(last=False)

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
            if event.type is EventType.APPROVAL_REQUIRED and record.status == "running":
                record.status = "awaiting_approval"
            elif (
                event.type is EventType.APPROVAL_RESOLVED
                and record.status not in ("cancelling", "finished")
            ):
                record.status = "running"
            envelope = RunEnvelope(
                id=record.next_event_id,
                type=event.type.value,
                run_id=record.run_id,
                session_id=record.session_id,
                ts=event.ts,
                agent=event.agent,
                data=event.data,
            )
            record.next_event_id += 1
            record.buffer.append(envelope)
            record.condition.notify_all()

    def events_after(self, run_id: str, after_id: int) -> list[RunEnvelope]:
        record = self.get(run_id)
        with record.condition:
            return [event for event in record.buffer if event.id > after_id]

    def wait_for_events(self, run_id: str, after_id: int, timeout: float = 15) -> bool:
        record = self.get(run_id)
        with record.condition:
            return record.condition.wait_for(
                lambda: any(event.id > after_id for event in record.buffer)
                or record.status == "finished",
                timeout,
            )

    def cancel(self, run_id: str) -> str:
        record = self.get(run_id)
        with record.condition:
            if record.status == "finished":
                return "already_finished"
            record.status = "cancelling"
            record.cancellation.request()
            record.condition.notify_all()

        if self.approval_gateway:
            self.approval_gateway.cancel_run(run_id)
        return "cancelling"

    def shutdown(self) -> None:
        with self._lock:
            self._shutting_down = True
            active = self._active
        if active is not None:
            self.cancel(active.run_id)
            if active.thread is not None:
                active.thread.join(timeout=5)

    @property
    def active(self) -> RunRecord | None:
        """Return the current lead run, if any."""
        with self._lock:
            return self._active

    def get(self, run_id: str) -> RunRecord:
        """Return a retained run record or raise when it is unknown."""
        with self._lock:
            record = self._records.get(run_id)
        if record is None:
            raise RunNotFoundError(run_id)
        return record
