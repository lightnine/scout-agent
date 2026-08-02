from scout.approval import (
    ApprovalKind,
    ApprovalRequest,
)


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
