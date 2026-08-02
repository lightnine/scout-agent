"""事件总线。

Agent 内部不直接 print——所有进展以事件形式发出，由订阅者决定怎么呈现：
CLI 渲染成彩色输出，Trace 落成 JSONL，将来接 Web 前端就推 SSE。
这一层解耦让"换个界面"不需要动 Agent 逻辑。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    RUN_START = "run_start"
    RUN_END = "run_end"
    STEP_START = "step_start"
    LLM_START = "llm_start"
    LLM_DELTA = "llm_delta"
    LLM_END = "llm_end"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    MEMORY_RECALL = "memory_recall"
    COMPACTION = "compaction"
    PLAN_UPDATED = "plan_updated"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_END = "subagent_end"
    ERROR = "error"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESOLVED = "approval_resolved"


@dataclass(slots=True)
class Event:
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    agent: str = "main"


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def emit(self, event_type: EventType | str, data: dict[str, Any] | None = None,
             agent: str = "main") -> None:
        event = Event(EventType(event_type), data or {}, agent=agent)
        for handler in self._handlers:
            try:
                handler(event)
            except Exception:  # 订阅者不该拖垮 Agent
                continue
