from conftest import FakeLLM, assistant_tool_call

from scout.llm.base import Message, Usage
from scout.runtime import Runtime
from scout.tools.plan import Plan


def test_session_restores_plan_and_usage(settings, fake_llm):
    runtime = Runtime(settings, llm=fake_llm, enable_trace=False)
    session = runtime.new_session("Research")
    session.plan = Plan(["search", "validate"], current=2)
    session.usage = Usage(prompt_tokens=30, completion_tokens=10, cached_tokens=5, calls=2)
    session.persist_state()

    resumed = runtime.resume_session(session.id)
    snapshot = runtime.session_snapshot(session.id)
    runtime.close()

    assert resumed.plan.steps == ["search", "validate"]
    assert resumed.plan.current == 2
    assert resumed.usage.prompt_tokens == 30
    assert snapshot["plan_steps"] == ["search", "validate"]
    assert snapshot["usage"]["calls"] == 2


def test_update_plan_persists_immediately_on_resume(settings):
    llm = FakeLLM(
        [
            assistant_tool_call("update_plan", {"steps": ["step1", "step2"], "current": 1}),
            Message(role="assistant", content="done"),
        ]
    )
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    runtime.build_agent(session).run("make a plan", stream=False)
    session_id = session.id
    runtime.close()

    runtime2 = Runtime(settings, llm=FakeLLM(), enable_trace=False)
    resumed = runtime2.resume_session(session_id)
    runtime2.close()

    assert resumed.plan.steps == ["step1", "step2"]
    assert resumed.plan.current == 1


def test_usage_accumulates_across_runs_without_double_counting(settings):
    llm = FakeLLM(
        [
            Message(role="assistant", content="first"),
            Message(role="assistant", content="second"),
        ]
    )
    runtime = Runtime(settings, llm=llm, enable_trace=False)
    session = runtime.new_session()
    agent = runtime.build_agent(session)

    agent.run("first", stream=False)
    assert session.usage.prompt_tokens == 10
    assert session.usage.completion_tokens == 5

    agent.run("second", stream=False)
    assert session.usage.prompt_tokens == 20
    assert session.usage.completion_tokens == 10

    session_id = session.id
    runtime.close()

    runtime2 = Runtime(settings, llm=FakeLLM(), enable_trace=False)
    resumed = runtime2.resume_session(session_id)
    runtime2.close()

    assert resumed.usage.prompt_tokens == 20
    assert resumed.usage.completion_tokens == 10


def test_persist_state_preserves_unrelated_meta(settings, fake_llm):
    runtime = Runtime(settings, llm=fake_llm, enable_trace=False)
    session = runtime.new_session()
    runtime.store.update_session_meta(session.id, {"custom_key": "keep_me"})
    session.plan = Plan(["a"], current=1)
    session.usage = Usage(prompt_tokens=5)
    session.persist_state()

    row = runtime.store.get_session(session.id)
    runtime.close()

    assert row["meta"]["custom_key"] == "keep_me"
    assert row["meta"]["plan"]["steps"] == ["a"]
    assert row["meta"]["usage"]["prompt_tokens"] == 5


def test_session_snapshot_shape_and_content(settings, fake_llm):
    runtime = Runtime(settings, llm=fake_llm, enable_trace=False)
    session = runtime.new_session("Title")
    session.plan = Plan(["s1"], current=1)
    session.usage = Usage(calls=1, prompt_tokens=10, completion_tokens=4, cached_tokens=2)
    session.persist_state()

    snapshot = runtime.session_snapshot(session.id)
    runtime.close()

    assert snapshot["id"] == session.id
    assert snapshot["title"] == "Title"
    assert snapshot["messages"] == []
    assert "[>] 1. s1" in snapshot["plan"]
    assert snapshot["plan_steps"] == ["s1"]
    assert snapshot["plan_current"] == 1
    assert snapshot["sources"] == []
    assert snapshot["usage"] == {
        "calls": 1,
        "prompt_tokens": 10,
        "completion_tokens": 4,
        "cached_tokens": 2,
    }
