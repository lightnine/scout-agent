"""Agent Loop：终止条件、工具回灌、重复保护、子 Agent、记忆注入。

全部用 FakeLLM 驱动，不联网、不花钱、结果确定。
"""

from __future__ import annotations

import pytest
from conftest import FakeLLM, assistant_tool_call

from scout.approval import ApprovalAction, ApprovalDecision
from scout.cancellation import RunCancellation
from scout.core.agent import Agent
from scout.core.events import EventType
from scout.llm.base import Message
from scout.memory.working import WorkingMemory
from scout.permissions import PolicyApprover
from scout.runtime import Runtime
from scout.tools import SUBAGENT_TOOLS


@pytest.fixture
def runtime_factory(settings):
    created: list[Runtime] = []

    def build(script):
        llm = FakeLLM(script)
        runtime = Runtime(settings, llm=llm, enable_trace=False)
        created.append(runtime)
        session = runtime.new_session()
        return runtime, llm, session, runtime.build_agent(session)

    yield build
    for runtime in created:
        runtime.close()


def test_loop_stops_when_model_returns_no_tool_calls(runtime_factory):
    _, llm, _, agent = runtime_factory(
        [
            assistant_tool_call("list_dir", {"path": "."}),
            Message(role="assistant", content="工作区里有 docs 目录。"),
        ]
    )
    result = agent.run("看看工作区里有什么", stream=False)

    assert result.text == "工作区里有 docs 目录。"
    assert result.steps == 2
    assert result.tool_calls == 1
    assert result.stop_reason == "completed"
    assert len(llm.received) == 2


def test_tool_result_is_fed_back_to_model(runtime_factory):
    _, llm, _, agent = runtime_factory(
        [
            assistant_tool_call("read_file", {"path": "docs/note.md"}),
            Message(role="assistant", content="读完了。"),
        ]
    )
    agent.run("读一下 docs/note.md", stream=False)

    second_round = llm.received[1]
    tool_messages = [m for m in second_round if m.role == "tool"]
    assert len(tool_messages) == 1
    assert "向量数据库调研笔记" in tool_messages[0].content
    assert tool_messages[0].tool_call_id == "c1"


def test_failed_tool_does_not_break_loop(runtime_factory):
    _, llm, _, agent = runtime_factory(
        [
            assistant_tool_call("read_file", {"path": "不存在.md"}),
            Message(role="assistant", content="那个文件不存在。"),
        ]
    )
    result = agent.run("读一下不存在.md", stream=False)

    tool_message = [m for m in llm.received[1] if m.role == "tool"][0]
    assert "文件不存在" in tool_message.content
    assert result.stop_reason == "completed"


def test_max_steps_forces_final_answer_without_tools(settings, runtime_factory):
    settings.max_steps = 2
    _, llm, _, agent = runtime_factory(
        [
            assistant_tool_call("list_dir", {"path": "."}, call_id="a"),
            assistant_tool_call("list_dir", {"path": "docs"}, call_id="b"),
        ]
    )
    result = agent.run("一直查下去", stream=False)

    assert result.stop_reason == "max_steps"
    assert llm.tools_seen[-1] is None, "最后一步不应再提供工具"
    assert "最大步数" in llm.received[-1][-1].content


def test_repeated_identical_calls_are_blocked(runtime_factory):
    same = {"path": "."}
    _, llm, _, agent = runtime_factory(
        [
            assistant_tool_call("list_dir", same, call_id="c1"),
            assistant_tool_call("list_dir", same, call_id="c2"),
            assistant_tool_call("list_dir", same, call_id="c3"),
            assistant_tool_call("list_dir", same, call_id="c4"),
            Message(role="assistant", content="好吧，我换个思路。"),
        ]
    )
    agent.run("重复调用", stream=False)

    blocked = [
        m.content
        for round_ in llm.received
        for m in round_
        if m.role == "tool" and "重复不会得到不同结果" in m.content
    ]
    assert blocked, "第 4 次完全相同的调用应该被拦截"


def test_parallel_tool_calls_all_get_responses(runtime_factory):
    from scout.llm.base import ToolCall

    multi = Message(
        role="assistant",
        tool_calls=[
            ToolCall(id="x1", name="list_dir", arguments={"path": "."}),
            ToolCall(id="x2", name="read_file", arguments={"path": "docs/note.md"}),
        ],
    )
    _, llm, _, agent = runtime_factory([multi, Message(role="assistant", content="都看完了。")])
    agent.run("同时做两件事", stream=False)

    tool_ids = {m.tool_call_id for m in llm.received[1] if m.role == "tool"}
    assert tool_ids == {"x1", "x2"}, "每个 tool_call 都必须有对应的结果消息"


def test_long_term_memory_is_injected_into_runtime_reminder(runtime_factory):
    runtime, llm, _, agent = runtime_factory([Message(role="assistant", content="好的。")])
    runtime.memory.save("用户在做数据平台，关注 Agent 落地", tags="画像")

    agent.run("我平时关注什么", stream=False)

    assert llm.received[0][0].role == "system"
    reminder = llm.received[0][-1].content
    assert "用户在做数据平台" in reminder


def test_plan_appears_in_runtime_reminder(runtime_factory):
    _, llm, session, agent = runtime_factory(
        [
            assistant_tool_call("update_plan", {"steps": ["查资料", "写报告"], "current": 1}),
            Message(role="assistant", content="计划定好了。"),
        ]
    )
    agent.run("先做个计划", stream=False)

    assert session.plan.steps == ["查资料", "写报告"]
    assert llm.received[0][0].content == llm.received[1][0].content, "static system 应整轮不变"
    assert "[>] 1. 查资料" in llm.received[1][-1].content


def test_subagent_runs_in_isolated_context(runtime_factory):
    _, llm, session, agent = runtime_factory(
        [
            assistant_tool_call("research_subtopic", {"topic": "Milvus 的适用场景"}),
            Message(role="assistant", content="子调研员查到：Milvus 适合十亿级检索。"),
            Message(role="assistant", content="综合来看，Milvus 适合大规模场景。"),
        ]
    )
    result = agent.run("帮我查 Milvus", stream=False)

    assert session.subagents_used == 1
    assert result.text == "综合来看，Milvus 适合大规模场景。"

    worker_prompt = llm.received[1][0].content
    assert "子调研员" in worker_prompt, "子 Agent 应使用自己的 system prompt"
    assert "Milvus 的适用场景" in llm.received[1][1].content

    tool_message = [m for m in llm.received[2] if m.role == "tool"][0]
    assert "Milvus 适合十亿级检索" in tool_message.content


def test_subagent_quota_is_enforced(settings, runtime_factory):
    settings.max_subagents = 0
    _, llm, _, agent = runtime_factory(
        [
            assistant_tool_call("research_subtopic", {"topic": "任意课题"}),
            Message(role="assistant", content="我自己来吧。"),
        ]
    )
    agent.run("派子 Agent", stream=False)

    tool_message = [m for m in llm.received[1] if m.role == "tool"][0]
    assert "配额已用完" in tool_message.content


def test_conversation_is_persisted_and_resumable(runtime_factory, settings):
    runtime, _, session, agent = runtime_factory(
        [
            assistant_tool_call("list_dir", {"path": "."}),
            Message(role="assistant", content="看完了。"),
        ]
    )
    agent.run("看看有什么", stream=False)

    stored = runtime.store.load_messages(session.id)
    assert [m["role"] for m in stored] == ["user", "assistant", "tool", "assistant"]

    resumed = runtime.resume_session(session.id)
    assert len(resumed.working.messages) == 4
    assert resumed.working.messages[0].content == "看看有什么"
    assert resumed.title == "看看有什么"


def test_streaming_deltas_are_emitted(runtime_factory):
    from scout.core.events import EventType

    runtime, _, _, agent = runtime_factory([Message(role="assistant", content="流式回答")])
    seen: list[str] = []
    runtime.bus.subscribe(
        lambda e: seen.append(e.data["text"]) if e.type is EventType.LLM_DELTA else None
    )

    agent.run("你好", stream=True)
    assert "".join(seen) == "流式回答"


def test_agent_run_propagates_run_id_to_approval_requests(settings):
    captured: list = []
    run_start_ids: list[str] = []

    class CaptureGateway:
        def request(self, request, emit=None):
            captured.append(request)
            return ApprovalDecision(ApprovalAction.APPROVE)

    settings.permission_mode = "ask"
    llm = FakeLLM(
        [
            assistant_tool_call("write_file", {"path": "out.txt", "content": "hi"}),
            Message(role="assistant", content="写好了。"),
        ]
    )
    runtime = Runtime(
        settings,
        llm=llm,
        approver=PolicyApprover("ask", gateway=CaptureGateway()),
        enable_trace=False,
    )
    runtime.bus.subscribe(
        lambda e: run_start_ids.append(e.data["run_id"])
        if e.type is EventType.RUN_START
        else None
    )
    try:
        session = runtime.new_session()
        agent = runtime.build_agent(session)
        agent.run("写个文件", stream=False, run_id="requested-run-id")
    finally:
        runtime.close()

    assert len(run_start_ids) == 1
    assert len(captured) == 1
    assert captured[0].run_id
    assert captured[0].session_id == session.id
    assert captured[0].run_id == "requested-run-id"
    assert run_start_ids[0] == "requested-run-id"
    assert agent.registry.ctx.run_id == "requested-run-id"


def test_worker_preserves_parent_run_id_for_later_risky_tool(settings):
    captured: list = []

    class CaptureGateway:
        def request(self, request, emit=None):
            captured.append(request)
            return ApprovalDecision(ApprovalAction.APPROVE)

    settings.permission_mode = "ask"
    llm = FakeLLM(
        [
            assistant_tool_call("research_subtopic", {"topic": "delegated research"}, "sub"),
            Message(role="assistant", content="worker result"),
            assistant_tool_call("write_file", {"path": "out.txt", "content": "hi"}, "write"),
            Message(role="assistant", content="done"),
        ]
    )
    runtime = Runtime(
        settings,
        llm=llm,
        approver=PolicyApprover("ask", gateway=CaptureGateway()),
        enable_trace=False,
    )
    try:
        session = runtime.new_session()
        runtime.build_agent(session).run("research", stream=False, run_id="parent-run")
    finally:
        runtime.close()

    assert len(captured) == 1
    assert captured[0].run_id == "parent-run"


def test_worker_start_does_not_clear_parent_cancellation(settings):
    runtime = Runtime(settings, llm=FakeLLM(), enable_trace=False)
    try:
        session = runtime.new_session()
        parent = runtime.build_agent(session)
        cancellation = RunCancellation()
        cancellation.request()
        parent.registry.ctx.run_id = "parent-run"
        worker = Agent(
            llm=parent.llm,
            settings=settings,
            session=session,
            registry=parent.registry.subset(SUBAGENT_TOOLS),
            bus=runtime.bus,
            name="worker-0",
            role="worker",
            working=WorkingMemory(threshold=settings.compact_threshold),
            persist=False,
            cancellation=cancellation,
        )

        result = worker.run(
            "delegated research",
            stream=False,
            run_id="parent-run",
            reset_cancellation=False,
        )
    finally:
        runtime.close()

    assert result.stop_reason == "cancelled"
    assert cancellation.is_cancelled() is True
    assert parent.registry.ctx.run_id == "parent-run"
