"""会话：一次调研任务的全部状态。

会话是可持久化、可恢复的——消息序列写进 SQLite，证据库按 session_id 隔离，
所以关掉终端第二天可以 ``/resume`` 接着聊，Agent 记得之前查到了什么。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..llm.base import Message, Usage
from ..memory.evidence import EvidenceStore
from ..memory.store import Store
from ..memory.working import WorkingMemory
from ..tools.plan import Plan


@dataclass
class Session:
    id: str
    store: Store
    working: WorkingMemory
    evidence: EvidenceStore
    title: str = ""
    plan: Plan = field(default_factory=Plan)
    usage: Usage = field(default_factory=Usage)
    subagents_used: int = 0
    created_at: float = field(default_factory=time.time)

    @classmethod
    def create(
        cls,
        store: Store,
        llm,
        compact_threshold: int = 16000,
        title: str = "",
    ) -> Session:
        session_id = store.create_session(title)
        return cls(
            id=session_id,
            store=store,
            working=WorkingMemory(threshold=compact_threshold),
            evidence=EvidenceStore(store, session_id, llm),
            title=title,
        )

    @classmethod
    def resume(cls, store: Store, llm, session_id: str, compact_threshold: int = 16000) -> Session:
        row = store.get_session(session_id)
        if row is None:
            raise ValueError(f"找不到会话 {session_id}")
        payloads = store.load_messages(session_id)
        working = WorkingMemory(threshold=compact_threshold)
        working.extend([Message.from_dict(p) for p in payloads])
        plan, usage = cls._state_from_meta(row.get("meta", {}))
        return cls(
            id=session_id,
            store=store,
            working=working,
            evidence=EvidenceStore(store, session_id, llm),
            title=row.get("title", ""),
            plan=plan,
            usage=usage,
            created_at=row.get("created_at", time.time()),
        )

    @staticmethod
    def _state_from_meta(meta: dict) -> tuple[Plan, Usage]:
        plan_data = meta.get("plan", {})
        usage_data = meta.get("usage", {})
        return (
            Plan(list(plan_data.get("steps", [])), int(plan_data.get("current", 0))),
            Usage(
                prompt_tokens=int(usage_data.get("prompt_tokens", 0)),
                completion_tokens=int(usage_data.get("completion_tokens", 0)),
                cached_tokens=int(usage_data.get("cached_tokens", 0)),
                calls=int(usage_data.get("calls", 0)),
            ),
        )

    def persist_state(self) -> None:
        stored = self.store.get_session(self.id) or {}
        meta = dict(stored.get("meta", {}))
        meta.update(
            {
                "plan": {"steps": self.plan.steps, "current": self.plan.current},
                "usage": {
                    "prompt_tokens": self.usage.prompt_tokens,
                    "completion_tokens": self.usage.completion_tokens,
                    "cached_tokens": self.usage.cached_tokens,
                    "calls": self.usage.calls,
                },
            }
        )
        self.store.update_session_meta(self.id, meta)

    def persist(self, messages: list[Message]) -> None:
        """只追加新产生的消息。压缩会改写内存里的消息序列，
        但**数据库里保留完整原始记录**——压缩是为了省上下文，不是为了删历史。"""
        self.store.append_messages(self.id, [m.to_dict() for m in messages])
        self.store.touch_session(self.id, self.title or None)

    def set_title_from(self, text: str) -> None:
        if self.title:
            return
        self.title = text.strip().splitlines()[0][:60] if text.strip() else "未命名调研"
        self.store.touch_session(self.id, self.title)
