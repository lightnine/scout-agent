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
