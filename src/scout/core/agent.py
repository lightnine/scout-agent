"""Agent Loop —— 整个框架的心脏。

一轮任务就是一个循环：**组装上下文 → 请求模型 → 执行工具 → 把结果写回上下文**，
直到模型不再请求工具（说明它认为任务完成），或者撞上步数上限。

看起来简单，真正决定成败的是循环里的那些防护：上下文压缩、重复调用检测、
步数耗尽时的收尾、工具异常不中断循环。少一个，Agent 就会在长任务上翻车。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..approval import ApprovalAction, ApprovalGateway, ApprovalKind, ApprovalRequest
from ..cancellation import RunCancellation, RunCancelled
from ..llm.base import LLMClient, Message, ToolCall, Usage
from ..memory.working import WorkingMemory
from ..tools import SUBAGENT_TOOLS
from ..tools.registry import ToolRegistry
from .events import EventBus, EventType
from .prompts import (
    FORCE_FINAL,
    build_runtime_reminder,
    build_static_system_prompt,
    build_worker_runtime_reminder,
    build_worker_static_prompt,
    format_run_timestamp,
)
from .session import Session

REPEAT_LIMIT = 3


@dataclass(slots=True)
class AgentResult:
    text: str
    steps: int
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "completed"  # completed | max_steps | error
    tool_calls: int = 0


@dataclass(slots=True)
class _RunState:
    plan_confirmed: bool
    plan_submitted: bool = False


class Agent:
    def __init__(
        self,
        llm: LLMClient,
        settings: Any,
        session: Session,
        registry: ToolRegistry,
        bus: EventBus | None = None,
        *,
        memory: Any = None,
        name: str = "main",
        role: str = "lead",
        working: WorkingMemory | None = None,
        max_steps: int | None = None,
        model: str | None = None,
        persist: bool = True,
        approval_gateway: ApprovalGateway | None = None,
        cancellation: RunCancellation | None = None,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.session = session
        self.registry = registry
        self.bus = bus or EventBus()
        self.memory = memory
        self.name = name
        self.role = role
        self.working = working or session.working
        self.max_steps = max_steps or settings.max_steps
        self.model = model
        self.persist = persist
        self.approval_gateway = approval_gateway
        self.cancellation = cancellation or RunCancellation()

    # ------------------------------------------------------------------ 主循环
    def run(
        self,
        user_input: str,
        stream: bool = True,
        run_id: str | None = None,
        *,
        reset_cancellation: bool = True,
    ) -> AgentResult:
        run_id = run_id or uuid.uuid4().hex[:8]
        if reset_cancellation:
            self.cancellation.clear()
        self.registry.ctx.run_id = run_id
        self.registry.ctx.cancellation = self.cancellation
        state = _RunState(
            plan_confirmed=self.role != "lead" or self.approval_gateway is None
        )
        run_started_at = format_run_timestamp()
        self._emit(EventType.RUN_START, {"run_id": run_id, "input": user_input})

        if self.role == "lead":
            self.session.set_title_from(user_input)

        memories = self._recall(user_input)
        user_message = Message(role="user", content=user_input)
        self.working.add(user_message)
        pending: list[Message] = [user_message]

        usage = Usage()
        final_text = ""
        stop_reason = "completed"
        tool_call_count = 0
        signatures: list[str] = []
        step = 0

        try:
            for step in range(1, self.max_steps + 1):
                self.cancellation.ensure_active()
                self._emit(EventType.STEP_START, {"step": step, "max": self.max_steps})
                self._maybe_compact()

                last_step = step == self.max_steps
                messages = self._assemble(memories, force_final=last_step, run_started_at=run_started_at)

                self._emit(EventType.LLM_START, {"step": step, "messages": len(messages)})
                response = self.llm.chat(
                    messages,
                    tools=None if last_step else self.registry.cached_schemas(),
                    stream=stream,
                    on_delta=self._on_delta if stream else None,
                    model=self.model,
                )
                usage = usage + response.usage
                self._emit(
                    EventType.LLM_END,
                    {
                        "step": step,
                        "content": response.message.content,
                        "tool_calls": [tc.name for tc in response.message.tool_calls],
                        "prompt_tokens": response.usage.prompt_tokens,
                        "completion_tokens": response.usage.completion_tokens,
                        "cached_tokens": response.usage.cached_tokens,
                        "latency_ms": response.latency_ms,
                    },
                )

                assistant = response.message
                self.working.add(assistant)
                pending.append(assistant)

                if not assistant.tool_calls:
                    final_text = assistant.content
                    stop_reason = "max_steps" if last_step else "completed"
                    break

                tool_call_count += len(assistant.tool_calls)
                self.cancellation.ensure_active()
                tool_messages = self._run_tools(assistant.tool_calls, signatures, run_id, state)
                self.working.extend(tool_messages)
                pending.extend(tool_messages)
            else:
                stop_reason = "max_steps"
                final_text = "已达到步数上限，未能得出最终结论。"
        except RunCancelled:
            stop_reason = "cancelled"
            final_text = "运行已取消。"
        except Exception as exc:
            stop_reason = "error"
            final_text = f"执行出错：{type(exc).__name__}: {exc}"
            self._emit(EventType.ERROR, {"error": str(exc), "type": type(exc).__name__})

        if self.persist:
            self.session.usage = self.session.usage + usage
            self.session.persist(pending)
            self.session.persist_state()

        result = AgentResult(final_text, step, usage, stop_reason, tool_call_count)
        self._emit(
            EventType.RUN_END,
            {
                "run_id": run_id,
                "steps": step,
                "stop_reason": stop_reason,
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "tool_calls": tool_call_count,
            },
        )
        return result

    # ------------------------------------------------------------------ 各环节
    def _assemble(self, memories: list, force_final: bool, run_started_at: str) -> list[Message]:
        """组装 cache-friendly 的消息序列。

        静态 system 前缀整轮不变；计划/来源/配额等 volatile 状态放在 messages 末尾的
        runtime reminder 里，避免每步 invalidate prefix cache。
        """
        tool_names = [t.name for t in self.registry.tools]
        if self.role == "worker":
            system = build_worker_static_prompt(self.settings)
            messages = [Message(role="system", content=system), *self.working.messages]
            messages.append(
                Message(
                    role="user",
                    content=build_worker_runtime_reminder(
                        self.settings, run_started_at=run_started_at
                    ),
                )
            )
        else:
            system = build_static_system_prompt(self.settings, tool_names)
            messages = [Message(role="system", content=system), *self.working.messages]
            messages.append(
                Message(
                    role="user",
                    content=build_runtime_reminder(
                        self.session,
                        self.settings,
                        memories,
                        run_started_at=run_started_at,
                    ),
                )
            )
        if force_final:
            messages.append(Message(role="user", content=FORCE_FINAL))
        return messages

    def _recall(self, query: str) -> list:
        if self.memory is None or self.role == "worker":
            return []
        try:
            hits = self.memory.search(query, limit=5)
        except Exception:
            return []
        if hits:
            self._emit(
                EventType.MEMORY_RECALL,
                {"count": len(hits), "items": [h.content for h in hits]},
            )
        return hits

    def _maybe_compact(self) -> None:
        if not self.working.needs_compaction():
            return
        result = self.working.compact(self.llm)
        if result:
            self._emit(
                EventType.COMPACTION,
                {
                    "tokens_before": result.tokens_before,
                    "tokens_after": result.tokens_after,
                    "dropped": result.messages_dropped,
                },
            )

    def _run_tools(
        self,
        calls: list[ToolCall],
        signatures: list[str],
        run_id: str,
        state: _RunState,
    ) -> list[Message]:
        blocked: dict[str, str] = {}
        for call in calls:
            signature = f"{call.name}:{sorted(call.arguments.items())}"
            if signatures.count(signature) >= REPEAT_LIMIT:
                # 模型陷入循环时，与其让它继续烧钱，不如明确告诉它换路子
                blocked[call.id] = (
                    f"你已经用完全相同的参数调用过 {call.name} {REPEAT_LIMIT} 次了。"
                    "重复不会得到不同结果，请换个思路：调整参数、换个来源，"
                    "或者基于现有信息直接给结论。"
                )
            signatures.append(signature)

        runnable = [c for c in calls if c.id not in blocked]
        requires_plan_confirmation = (
            self.role == "lead"
            and self.approval_gateway is not None
            and not state.plan_confirmed
        )
        plan_calls = (
            [call for call in runnable if call.name == "update_plan"]
            if requires_plan_confirmation
            else []
        )
        deferred: dict[str, str] = {}
        if plan_calls:
            allowed_ids = {call.id for call in plan_calls}
            for call in runnable:
                if call.id not in allowed_ids:
                    deferred[call.id] = "计划尚未确认，请在确认后重新调用该工具。"
            runnable = plan_calls
        elif state.plan_submitted and requires_plan_confirmation:
            deferred = {
                call.id: "计划尚未确认，请提交修改后的计划并等待确认。" for call in runnable
            }
            runnable = []

        self.cancellation.ensure_active()
        for call in runnable:
            self._emit(
                EventType.TOOL_START,
                {"tool": call.name, "arguments": call.arguments, "id": call.id},
            )
        results = self.registry.execute_batch(runnable) if runnable else []
        plan_feedback = ""
        if plan_calls and any(result.ok for result in results):
            state.plan_submitted = True
            request = ApprovalRequest.create(
                run_id,
                self.session.id,
                ApprovalKind.PLAN,
                "调研计划待确认",
                {"plan": self.session.plan.render(), "steps": self.session.plan.steps},
            )
            decision = self.approval_gateway.request(
                request,
                lambda kind, data: self.bus.emit(kind, data, agent=self.name),
            )
            if decision.action is ApprovalAction.APPROVE:
                state.plan_confirmed = True
            elif decision.action is ApprovalAction.CANCEL:
                raise RunCancelled("用户取消运行")
            else:
                feedback = decision.feedback or "请重新制定计划"
                plan_feedback = f"[计划未确认] 用户修改意见：{feedback}\n请重拟计划并再次提交。"

        messages: list[Message] = []
        by_id = {call.id: result for call, result in zip(runnable, results, strict=True)}
        for call in calls:
            if call.id in blocked:
                content, display, ok = blocked[call.id], "被重复调用保护拦截", False
            elif call.id in deferred:
                content, display, ok = deferred[call.id], "等待计划确认", False
            else:
                result = by_id[call.id]
                content, display, ok = result.content, result.display, result.ok
                if plan_feedback and call.id in {plan_call.id for plan_call in plan_calls}:
                    content = f"{content}\n{plan_feedback}"
                self._emit(
                    EventType.TOOL_END,
                    {
                        "tool": call.name,
                        "ok": ok,
                        "display": display,
                        "duration_ms": result.meta.get("duration_ms", 0),
                    },
                )
            messages.append(
                Message(role="tool", content=content, tool_call_id=call.id, name=call.name)
            )
        return messages

    def _on_delta(self, text: str) -> None:
        self._emit(EventType.LLM_DELTA, {"text": text})

    def _emit(self, event_type: EventType, data: dict) -> None:
        self.bus.emit(event_type, data, agent=self.name)

    # ---------------------------------------------------------------- 子 Agent
    def make_spawner(self) -> Callable[[str], str]:
        """返回一个"派生子调研员"的函数，注入到 ToolContext 里给 delegate 工具用。

        子 Agent 与主 Agent **共享证据库和会话**（这样抓到的资料主 Agent 也能引用），
        但**拥有独立的工作记忆**——这正是它能节省主上下文的原因。
        """

        def spawn(brief: str) -> str:
            index = self.session.subagents_used
            name = f"worker-{index}"
            self._emit(EventType.SUBAGENT_START, {"worker": name, "brief": brief})

            worker = Agent(
                llm=self.llm,
                settings=self.settings,
                session=self.session,
                registry=self.registry.subset(SUBAGENT_TOOLS),
                bus=self.bus,
                name=name,
                role="worker",
                working=WorkingMemory(threshold=self.settings.compact_threshold),
                max_steps=self.settings.subagent_max_steps,
                model=self.settings.worker_model(),
                persist=False,
                approval_gateway=None,
                cancellation=self.cancellation,
            )
            result = worker.run(
                brief,
                stream=False,
                run_id=self.registry.ctx.run_id,
                reset_cancellation=False,
            )
            self._emit(
                EventType.SUBAGENT_END,
                {"worker": name, "steps": result.steps, "tokens": result.usage.total},
            )
            return result.text or "（子调研员没有产出有效结论）"

        return spawn
