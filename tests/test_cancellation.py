import pytest
from conftest import FakeLLM, assistant_tool_call

from scout.cancellation import RunCancellation, RunCancelled
from scout.core.events import EventType
from scout.llm.base import Message
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
