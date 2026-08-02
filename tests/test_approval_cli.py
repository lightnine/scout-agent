import pytest

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


def request(kind, *, run_id="run-1", session_id="session-1"):
    payload = {"plan": "[ ] 1. Search"} if kind is ApprovalKind.PLAN else {
        "tool": "write_file",
        "arguments": {"path": "out.txt"},
        "risk": 1,
    }
    return ApprovalRequest.create(run_id, session_id, kind, "Confirm", payload)


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (["y"], ApprovalAction.APPROVE),
        (["e", "Add source validation"], ApprovalAction.REVISE),
        (["c"], ApprovalAction.CANCEL),
    ],
)
def test_plan_actions(answers, expected):
    gateway = CliApprovalGateway(FakeConsole(answers))
    decision = gateway.request(request(ApprovalKind.PLAN))
    assert decision.action is expected
    if expected is ApprovalAction.REVISE:
        assert decision.feedback == "Add source validation"


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        (["y"], ApprovalAction.APPROVE),
        (["n"], ApprovalAction.REJECT),
        (["a"], ApprovalAction.ALLOW_SESSION),
        (["c"], ApprovalAction.CANCEL),
    ],
)
def test_tool_actions(answers, expected):
    gateway = CliApprovalGateway(FakeConsole(answers))
    decision = gateway.request(request(ApprovalKind.TOOL))
    assert decision.action is expected


def test_emit_approval_events():
    events: list[tuple[str, dict]] = []

    def emit(kind, data):
        events.append((kind, data))

    req = request(ApprovalKind.PLAN, run_id="run-42", session_id="sess-7")
    decision = CliApprovalGateway(FakeConsole(["y"])).request(req, emit=emit)

    assert decision.action is ApprovalAction.APPROVE
    assert len(events) == 2
    assert events[0][0] == "approval_required"
    required = events[0][1]
    assert required["approval_id"] == req.id
    assert required["run_id"] == "run-42"
    assert required["session_id"] == "sess-7"

    assert events[1][0] == "approval_resolved"
    resolved = events[1][1]
    assert resolved["approval_id"] == req.id
    assert resolved["run_id"] == "run-42"
    assert resolved["session_id"] == "sess-7"
    assert resolved["action"] == ApprovalAction.APPROVE.value
