import threading
import time

import pytest

from scout.approval import ApprovalAction, ApprovalDecision, ApprovalKind, ApprovalRequest
from scout.web.gateway import (
    ApprovalAlreadyResolvedError,
    ApprovalNotFoundError,
    WebApprovalGateway,
)


def wait_for_pending(gateway, run_id):
    deadline = time.monotonic() + 1
    while not gateway.pending_for_run(run_id):
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_request_blocks_until_resolved():
    gateway = WebApprovalGateway()
    request = ApprovalRequest.create("r1", "s1", ApprovalKind.PLAN, "Plan", {})
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", gateway.request(request)))
    thread.start()
    wait_for_pending(gateway, "r1")

    gateway.resolve(request.id, ApprovalDecision(ApprovalAction.APPROVE))
    thread.join(1)

    assert thread.is_alive() is False
    assert result["value"].action is ApprovalAction.APPROVE
    assert gateway.pending_for_run("r1") == []
    with pytest.raises(ApprovalAlreadyResolvedError):
        gateway.resolve(request.id, ApprovalDecision(ApprovalAction.REJECT))


def test_cancel_run_unblocks_pending_request():
    gateway = WebApprovalGateway()
    request = ApprovalRequest.create("r1", "s1", ApprovalKind.TOOL, "Tool", {})
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", gateway.request(request)))
    thread.start()
    wait_for_pending(gateway, "r1")

    gateway.cancel_run("r1")
    thread.join(1)

    assert thread.is_alive() is False
    assert result["value"].action is ApprovalAction.CANCEL


def test_request_emits_required_before_waiting_and_resolved_after_decision():
    gateway = WebApprovalGateway()
    request = ApprovalRequest.create("r1", "s1", ApprovalKind.PLAN, "Plan", {})
    events = []
    thread = threading.Thread(
        target=lambda: gateway.request(request, emit=lambda kind, data: events.append((kind, data)))
    )
    thread.start()
    wait_for_pending(gateway, "r1")

    assert events == [("approval_required", request.event_data())]
    gateway.resolve(request.id, ApprovalDecision(ApprovalAction.APPROVE))
    thread.join(1)

    assert events[1] == (
        "approval_resolved",
        {
            "approval_id": request.id,
            "run_id": "r1",
            "session_id": "s1",
            "action": "approve",
        },
    )


def test_unknown_approval_cannot_be_resolved():
    gateway = WebApprovalGateway()

    with pytest.raises(ApprovalNotFoundError):
        gateway.resolve("missing", ApprovalDecision(ApprovalAction.APPROVE))


def test_resolve_and_cancel_race_returns_one_deterministic_decision():
    gateway = WebApprovalGateway()
    request = ApprovalRequest.create("r1", "s1", ApprovalKind.TOOL, "Tool", {})
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", gateway.request(request)))
    thread.start()
    wait_for_pending(gateway, "r1")

    start = threading.Barrier(3)
    resolve_errors = []

    def resolve():
        start.wait()
        try:
            gateway.resolve(request.id, ApprovalDecision(ApprovalAction.APPROVE))
        except ApprovalAlreadyResolvedError as error:
            resolve_errors.append(error)

    resolve_thread = threading.Thread(target=resolve)
    cancel_thread = threading.Thread(target=lambda: (start.wait(), gateway.cancel_run("r1")))
    resolve_thread.start()
    cancel_thread.start()
    start.wait()
    resolve_thread.join(1)
    cancel_thread.join(1)
    thread.join(1)

    assert resolve_thread.is_alive() is False
    assert cancel_thread.is_alive() is False
    assert thread.is_alive() is False
    assert result["value"].action in (ApprovalAction.APPROVE, ApprovalAction.CANCEL)
    assert len(resolve_errors) <= 1
    with pytest.raises(ApprovalAlreadyResolvedError):
        gateway.resolve(request.id, ApprovalDecision(ApprovalAction.REJECT))
