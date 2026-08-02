"""CLI 审批：ask/readonly 通过 CliApprovalGateway 放行/拒绝 risky 工具。"""

from __future__ import annotations

from scout.approval import ApprovalAction, ApprovalKind, ApprovalRequest
from scout.approval_cli import CliApprovalGateway
from scout.permissions import PolicyApprover
from scout.tools.base import Risk, tool


@tool(risk=Risk.CAUTION)
def risky(value: str) -> str:
    """测试用 risky 工具。"""
    return value


class FakeConsole:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.printed: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.printed.append(" ".join(str(a) for a in args))

    def input(self, prompt: str) -> str:
        return self.answers.pop(0)


def _approver(console: FakeConsole, mode: str) -> PolicyApprover:
    gateway = None if mode == "auto" else CliApprovalGateway(console)
    return PolicyApprover(mode, gateway=gateway)


def test_cli_approver_uses_gateway_not_prompt_callback():
    console = FakeConsole(["y"])
    approver = _approver(console, "ask")
    assert isinstance(approver, PolicyApprover)
    assert callable(getattr(approver.gateway, "request", None))

    decision = approver.check(risky, {"value": "x"}, session_id="s1", run_id="r1")
    assert decision.allowed is True


def test_cli_approver_rejects_on_no():
    console = FakeConsole(["n"])
    approver = _approver(console, "ask")
    decision = approver.check(risky, {"value": "x"}, session_id="s1", run_id="r1")
    assert decision.allowed is False


def test_cli_approver_allow_session_skips_second_prompt():
    console = FakeConsole(["a"])
    approver = _approver(console, "ask")
    first = approver.check(risky, {"value": "a"}, session_id="s1", run_id="r1")
    second = approver.check(risky, {"value": "b"}, session_id="s1", run_id="r2")
    assert first.allowed and second.allowed
    assert len(console.answers) == 0


def test_cli_gateway_can_revise_a_plan():
    console = FakeConsole(["e", "add sources"])
    gateway = CliApprovalGateway(console)
    request = ApprovalRequest.create(
        "run-1",
        "session-1",
        ApprovalKind.PLAN,
        "调研计划待确认",
        {"plan": "1. inspect", "steps": ["inspect"]},
    )

    decision = gateway.request(request)

    assert decision.action is ApprovalAction.REVISE
    assert decision.feedback == "add sources"


def test_cli_gateway_can_confirm_or_cancel_a_plan():
    request = ApprovalRequest.create(
        "run-1",
        "session-1",
        ApprovalKind.PLAN,
        "调研计划待确认",
        {"plan": "1. inspect", "steps": ["inspect"]},
    )

    assert (
        CliApprovalGateway(FakeConsole(["y"])).request(request).action
        is ApprovalAction.APPROVE
    )
    assert (
        CliApprovalGateway(FakeConsole(["c"])).request(request).action
        is ApprovalAction.CANCEL
    )


def test_auto_mode_has_no_cli_plan_gateway():
    assert _approver(FakeConsole([]), "auto").gateway is None
