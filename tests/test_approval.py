import pytest

from scout.approval import (
    ApprovalAction,
    ApprovalDecision,
    ApprovalKind,
    ApprovalRequest,
)
from scout.cancellation import RunCancelled
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


def test_safe_policy_paths_and_session_scoped_allow():
    gateway = FixedGateway(ApprovalDecision(ApprovalAction.ALLOW_SESSION))
    approver = PolicyApprover("ask", gateway=gateway)

    first = approver.check(risky, {"value": "a"}, session_id="s1", run_id="r1")
    second = approver.check(risky, {"value": "b"}, session_id="s1", run_id="r2")
    other = approver.check(risky, {"value": "c"}, session_id="s2", run_id="r3")

    assert first.allowed and second.allowed and other.allowed
    assert len(gateway.requests) == 2
    assert gateway.requests[0].kind is ApprovalKind.TOOL
    assert gateway.requests[0].session_id == "s1"
    assert gateway.requests[0].run_id == "r1"
    assert gateway.requests[1].session_id == "s2"
    assert gateway.requests[1].run_id == "r3"


def test_reject_is_returned_to_model():
    gateway = FixedGateway(ApprovalDecision(ApprovalAction.REJECT, "not now"))
    decision = PolicyApprover("ask", gateway=gateway).check(
        risky, {"value": "a"}, session_id="s1", run_id="r1"
    )
    assert decision.allowed is False
    assert "not now" in decision.reason


def test_cancel_raises_run_cancelled():
    gateway = FixedGateway(ApprovalDecision(ApprovalAction.CANCEL))
    with pytest.raises(RunCancelled, match="用户取消运行"):
        PolicyApprover("ask", gateway=gateway).check(
            risky, {"value": "a"}, session_id="s1", run_id="r1"
        )
