"""Blocking approval gateway used by the web workbench."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from ..approval import ApprovalAction, ApprovalDecision, ApprovalRequest, Emitter


class ApprovalNotFoundError(KeyError):
    """Raised when an approval ID is not pending or previously resolved."""


class ApprovalAlreadyResolvedError(RuntimeError):
    """Raised when an approval decision has already been chosen."""


@dataclass
class _Pending:
    request: ApprovalRequest
    event: threading.Event = field(default_factory=threading.Event)
    decision: ApprovalDecision | None = None
    resolved: bool = False


class WebApprovalGateway:
    """Coordinate synchronous agent approval requests with web UI decisions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, _Pending] = {}
        self._resolved: set[str] = set()
        self._cancelled_runs: set[str] = set()

    def request(
        self,
        request: ApprovalRequest,
        emit: Emitter | None = None,
    ) -> ApprovalDecision:
        item = _Pending(request)
        with self._lock:
            if request.run_id in self._cancelled_runs:
                self._resolved.add(request.id)
                return ApprovalDecision(ApprovalAction.CANCEL)
            self._pending[request.id] = item

        if emit is not None:
            emit("approval_required", request.event_data())

        item.event.wait()

        with self._lock:
            self._pending.pop(request.id, None)
            self._resolved.add(request.id)
            decision = item.decision or ApprovalDecision(ApprovalAction.CANCEL)

        if emit is not None:
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
            self._cancelled_runs.add(run_id)
            items = [
                item
                for item in self._pending.values()
                if item.request.run_id == run_id and not item.resolved
            ]
            for item in items:
                item.resolved = True
                item.decision = ApprovalDecision(ApprovalAction.CANCEL)
                item.event.set()

    def run_finished(self, run_id: str) -> None:
        """Forget cancellation state after the run can no longer request approval."""
        with self._lock:
            self._cancelled_runs.discard(run_id)

    def pending_for_run(self, run_id: str) -> list[ApprovalRequest]:
        with self._lock:
            return [
                item.request
                for item in self._pending.values()
                if item.request.run_id == run_id and not item.resolved
            ]
