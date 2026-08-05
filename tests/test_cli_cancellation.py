"""CLI 协作式取消：Ctrl+C → RunCancellation。"""

from __future__ import annotations

import signal
from types import SimpleNamespace

import pytest
from conftest import FakeLLM, assistant_tool_call

from scout.cancellation import RunCancellation
from scout.cli import _install_run_cancel_handler, _restore_signal_handler
from scout.core.events import EventType
from scout.llm.base import Message
from scout.runtime import Runtime


@pytest.fixture
def agent_with_token():
    token = RunCancellation()
    return SimpleNamespace(cancellation=token)


def test_sigint_handler_requests_cancellation_once(agent_with_token):
    notices: list[int] = []
    previous = _install_run_cancel_handler(
        agent_with_token,
        on_cancel_requested=lambda: notices.append(1),
    )
    handler = signal.getsignal(signal.SIGINT)
    try:
        handler(signal.SIGINT, None)
        assert agent_with_token.cancellation.is_cancelled()
        assert notices == [1]
        with pytest.raises(KeyboardInterrupt):
            handler(signal.SIGINT, None)
    finally:
        _restore_signal_handler(previous)


def test_sigint_handler_restores_previous_handler(agent_with_token):
    def dummy_handler(_signum: int, _frame: object) -> None:
        return None

    signal.signal(signal.SIGINT, dummy_handler)
    previous = _install_run_cancel_handler(agent_with_token)
    try:
        assert signal.getsignal(signal.SIGINT) is not dummy_handler
    finally:
        _restore_signal_handler(previous)
    assert signal.getsignal(signal.SIGINT) is dummy_handler


def test_cancel_before_run_still_skips_llm(settings):
    """模拟 Ctrl+C 在 agent.run 开始前已 request，与 Web cancel 语义一致。"""
    token = RunCancellation()
    token.request()
    llm = FakeLLM([Message(role="assistant", content="must not run")])
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    agent = runtime.build_agent(session, cancellation=token)
    try:
        result = agent.run("question", stream=False, reset_cancellation=False)
    finally:
        runtime.close()

    assert result.stop_reason == "cancelled"
    assert not llm.received


def test_cancel_after_tool_via_handler(settings):
    """模拟运行中 Ctrl+C：第一步工具结束后 request，第二步 LLM 不再调用。"""
    token = RunCancellation()
    llm = FakeLLM(
        [
            assistant_tool_call("list_dir", {"path": "."}),
            Message(role="assistant", content="must not run"),
        ]
    )
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    agent = runtime.build_agent(session, cancellation=token)
    previous = _install_run_cancel_handler(agent)
    try:
        runtime.bus.subscribe(
            lambda event: token.request() if event.type is EventType.TOOL_END else None
        )
        result = agent.run("question", stream=False)
    finally:
        _restore_signal_handler(previous)
        runtime.close()

    assert result.stop_reason == "cancelled"
    assert len(llm.received) == 1
