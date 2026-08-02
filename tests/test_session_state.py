from scout.llm.base import Usage
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
