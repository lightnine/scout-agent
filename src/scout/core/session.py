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
        payloads = store.load_messages(session_id)
        working = WorkingMemory(threshold=compact_threshold)
        working.extend([Message.from_dict(p) for p in payloads])
        sessions = {s["id"]: s for s in store.list_sessions(limit=200)}
        meta = sessions.get(session_id)
        if meta is None:
            raise ValueError(f"找不到会话 {session_id}")
        return cls(
            id=session_id,
            store=store,
            working=working,
            evidence=EvidenceStore(store, session_id, llm),
            title=meta.get("title", ""),
        )

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
