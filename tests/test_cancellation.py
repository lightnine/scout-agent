import pytest
from conftest import FakeLLM, assistant_tool_call

from scout.approval import ApprovalAction, ApprovalDecision
from scout.cancellation import RunCancellation, RunCancelled
from scout.core.events import EventType
from scout.llm.base import Message, ToolCall
from scout.permissions import PolicyApprover
from scout.runtime import Runtime
from scout.tools.base import Risk, tool


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
            self.requests = []

        def request(self, request, emit=None):
            self.requests.append(request)
            return self.decisions.pop(0)

    side_effects = []

    @tool(name="counted_write", risk=Risk.CAUTION)
    def counted_write(value: str) -> str:
        """Record one deterministic side effect."""
        side_effects.append(value)
        return f"executed {value}"

    assistant = Message(
        role="assistant",
        tool_calls=[
            ToolCall(id="write-1", name="counted_write", arguments={"value": "first"}),
            ToolCall(id="write-2", name="counted_write", arguments={"value": "second"}),
            ToolCall(id="write-3", name="counted_write", arguments={"value": "third"}),
        ],
    )
    llm = FakeLLM([assistant, Message(role="assistant", content="must not run")])
    gateway = CancellingGateway()
    runtime = Runtime(
        settings,
        llm=llm,
        approver=PolicyApprover("ask", gateway=gateway),
        enable_trace=False,
    )
    session = runtime.new_session()
    agent = runtime.build_agent(session)
    agent.approval_gateway = None
    agent.registry.register(counted_write)

    try:
        result = agent.run("write three", stream=False)
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
    assert assistant_calls == ["write-1", "write-2", "write-3"]
    assert tool_results == assistant_calls
    stored_tools = [message for message in stored if message["role"] == "tool"]
    assert stored_tools[0]["content"] == "executed first"
    assert all("未执行" in message["content"] for message in stored_tools[1:])
    assert side_effects == ["first"]
    assert len(gateway.requests) == 2
    assert [message.tool_call_id for message in resumed.working.messages if message.role == "tool"] == [
        "write-1",
        "write-2",
        "write-3",
    ]


def test_pre_batch_cancellation_persists_not_executed_fallback(settings):
    token = RunCancellation()
    llm = FakeLLM(
        [
            assistant_tool_call("list_dir", {"path": "."}, call_id="never-started"),
            Message(role="assistant", content="must not run"),
        ]
    )
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    runtime.bus.subscribe(
        lambda event: token.request() if event.type is EventType.LLM_END else None
    )

    try:
        result = runtime.build_agent(session, cancellation=token).run("question", stream=False)
        stored = runtime.store.load_messages(session.id)
    finally:
        runtime.close()

    tool_messages = [message for message in stored if message["role"] == "tool"]
    assert result.stop_reason == "cancelled"
    assert len(llm.received) == 1
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "never-started"
    assert "未执行" in tool_messages[0]["content"]
