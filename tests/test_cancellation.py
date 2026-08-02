import pytest
from conftest import FakeLLM, assistant_tool_call

from scout.approval import ApprovalAction, ApprovalDecision
from scout.cancellation import RunCancellation, RunCancelled
from scout.core.events import EventType
from scout.llm.base import Message, ToolCall
from scout.permissions import PolicyApprover
from scout.runtime import Runtime


def test_cancellation_can_be_requested_and_cleared():
    token = RunCancellation()
    assert token.is_cancelled() is False
    token.request()
    assert token.is_cancelled() is True
    with pytest.raises(RunCancelled):
        token.ensure_active()
    token.clear()
    token.ensure_active()


def test_cancel_before_first_step_emits_cancelled_run_end(settings):
    llm = FakeLLM([Message(role="assistant", content="must not run")])
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    token = RunCancellation()
    token.request()
    seen = []
    runtime.bus.subscribe(lambda event: seen.append(event))
    try:
        result = runtime.build_agent(session, cancellation=token).run(
            "question", stream=False, reset_cancellation=False
        )
    finally:
        runtime.close()

    assert result.stop_reason == "cancelled"
    assert not llm.received
    assert next(event for event in seen if event.type is EventType.RUN_END).data["stop_reason"] == "cancelled"


def test_run_resets_injected_cancellation_by_default(settings):
    llm = FakeLLM([Message(role="assistant", content="ran")])
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    token = RunCancellation()
    token.request()
    try:
        result = runtime.build_agent(session, cancellation=token).run("question", stream=False)
    finally:
        runtime.close()

    assert result.stop_reason == "completed"
    assert len(llm.received) == 1
    assert token.is_cancelled() is False


def test_cancellation_after_tool_prevents_next_llm_step(settings):
    token = RunCancellation()
    llm = FakeLLM(
        [
            assistant_tool_call("list_dir", {"path": "."}),
            Message(role="assistant", content="must not run"),
        ]
    )
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    runtime.bus.subscribe(
        lambda event: token.request() if event.type is EventType.TOOL_END else None
    )
    try:
        result = runtime.build_agent(session, cancellation=token).run("question", stream=False)
    finally:
        runtime.close()

    assert result.stop_reason == "cancelled"
    assert len(llm.received) == 1


def test_cancellation_during_serial_batch_persists_complete_tool_protocol(settings):
    class CancellingGateway:
        def __init__(self):
            self.decisions = [
                ApprovalDecision(ApprovalAction.APPROVE),
                ApprovalDecision(ApprovalAction.CANCEL),
            ]

        def request(self, request, emit=None):
            return self.decisions.pop(0)

    assistant = Message(
        role="assistant",
        tool_calls=[
            ToolCall(id="write-1", name="write_file", arguments={"path": "one.txt", "content": "1"}),
            ToolCall(id="write-2", name="write_file", arguments={"path": "two.txt", "content": "2"}),
        ],
    )
    llm = FakeLLM([assistant, Message(role="assistant", content="must not run")])
    runtime = Runtime(
        settings,
        llm=llm,
        approver=PolicyApprover("ask", gateway=CancellingGateway()),
        enable_trace=False,
    )
    session = runtime.new_session()
    agent = runtime.build_agent(session)
    agent.approval_gateway = None

    try:
        result = agent.run("write both", stream=False)
        stored = runtime.store.load_messages(session.id)
        resumed = runtime.resume_session(session.id)
    finally:
        runtime.close()

    assistant_calls = [
        call["id"]
        for message in stored
        if message["role"] == "assistant"
        for call in message["tool_calls"]
    ]
    tool_results = [
        message["tool_call_id"] for message in stored if message["role"] == "tool"
    ]
    assert result.stop_reason == "cancelled"
    assert len(llm.received) == 1
    assert assistant_calls == ["write-1", "write-2"]
    assert tool_results == assistant_calls
    assert all(
        "取消" in message["content"] for message in stored if message["role"] == "tool"
    )
    assert [message.tool_call_id for message in resumed.working.messages if message.role == "tool"] == [
        "write-1",
        "write-2",
    ]
