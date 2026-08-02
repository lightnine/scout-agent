from conftest import FakeLLM, ScriptedGateway

from scout.approval import ApprovalAction, ApprovalDecision, ApprovalKind
from scout.core.events import EventType
from scout.llm.base import Message, ToolCall
from scout.runtime import Runtime


def test_plan_and_sibling_tool_batch_executes_only_plan(settings):
    gateway = ScriptedGateway([ApprovalDecision(ApprovalAction.APPROVE)])
    first = Message(
        role="assistant",
        tool_calls=[
            ToolCall("p1", "update_plan", {"steps": ["inspect", "report"], "current": 1}),
            ToolCall("f1", "list_dir", {"path": "."}),
        ],
    )
    llm = FakeLLM([first, Message(role="assistant", content="done")])
    runtime = Runtime(settings, llm=llm, approval_gateway=gateway, enable_trace=False)
    started_tools: list[str] = []
    runtime.bus.subscribe(
        lambda event: started_tools.append(event.data["tool"])
        if event.type is EventType.TOOL_START
        else None
    )
    session = runtime.new_session()
    try:
        runtime.build_agent(session).run("research", stream=False)
    finally:
        runtime.close()

    assert gateway.requests[0].kind is ApprovalKind.PLAN
    tool_messages = [m for m in llm.received[1] if m.role == "tool"]
    assert {m.tool_call_id for m in tool_messages} == {"p1", "f1"}
    assert "计划尚未确认" in next(m.content for m in tool_messages if m.tool_call_id == "f1")
    assert started_tools == ["update_plan"]


def test_revise_feedback_requires_a_second_plan_confirmation(settings):
    gateway = ScriptedGateway(
        [
            ApprovalDecision(ApprovalAction.REVISE, "add source validation"),
            ApprovalDecision(ApprovalAction.APPROVE),
        ]
    )
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall("p1", "update_plan", {"steps": ["inspect"], "current": 1})
                ],
            ),
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        "p2",
                        "update_plan",
                        {"steps": ["inspect", "validate"], "current": 1},
                    )
                ],
            ),
            Message(role="assistant", content="done"),
        ]
    )
    runtime = Runtime(settings, llm=llm, approval_gateway=gateway, enable_trace=False)
    session = runtime.new_session()
    try:
        runtime.build_agent(session).run("research", stream=False)
    finally:
        runtime.close()

    assert len(gateway.requests) == 2
    assert "add source validation" in next(
        m.content for m in llm.received[1] if m.tool_call_id == "p1"
    )


def test_approved_plan_updates_do_not_repeat_confirmation(settings):
    gateway = ScriptedGateway([ApprovalDecision(ApprovalAction.APPROVE)])
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall("p1", "update_plan", {"steps": ["inspect"], "current": 1})
                ],
            ),
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall("p2", "update_plan", {"steps": ["inspect"], "current": 1})
                ],
            ),
            Message(role="assistant", content="done"),
        ]
    )
    runtime = Runtime(settings, llm=llm, approval_gateway=gateway, enable_trace=False)
    session = runtime.new_session()
    try:
        runtime.build_agent(session).run("research", stream=False)
    finally:
        runtime.close()

    assert len(gateway.requests) == 1


def test_lead_without_plan_never_requests_confirmation(settings):
    gateway = ScriptedGateway([])
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                tool_calls=[ToolCall("f1", "list_dir", {"path": "."})],
            ),
            Message(role="assistant", content="done"),
        ]
    )
    runtime = Runtime(settings, llm=llm, approval_gateway=gateway, enable_trace=False)
    session = runtime.new_session()
    try:
        runtime.build_agent(session).run("research", stream=False)
    finally:
        runtime.close()

    assert gateway.requests == []


def test_worker_never_requests_plan_confirmation(settings):
    gateway = ScriptedGateway([])
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall("p1", "update_plan", {"steps": ["inspect"], "current": 1})
                ],
            ),
            Message(role="assistant", content="done"),
        ]
    )
    runtime = Runtime(settings, llm=llm, approval_gateway=gateway, enable_trace=False)
    session = runtime.new_session()
    try:
        agent = runtime.build_agent(session)
        agent.role = "worker"
        agent.run("research", stream=False)
    finally:
        runtime.close()

    assert gateway.requests == []


def test_cancelled_plan_stops_the_run(settings):
    gateway = ScriptedGateway([ApprovalDecision(ApprovalAction.CANCEL)])
    llm = FakeLLM(
        [
            Message(
                role="assistant",
                tool_calls=[
                    ToolCall("p1", "update_plan", {"steps": ["inspect"], "current": 1})
                ],
            )
        ]
    )
    runtime = Runtime(settings, llm=llm, approval_gateway=gateway, enable_trace=False)
    session = runtime.new_session()
    try:
        result = runtime.build_agent(session).run("research", stream=False)
    finally:
        runtime.close()

    assert result.stop_reason == "cancelled"
    assert result.text == "运行已取消。"
