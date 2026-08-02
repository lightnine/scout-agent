from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ApprovalKind(StrEnum):
    PLAN = "plan"
    TOOL = "tool"


class ApprovalAction(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"
    ALLOW_SESSION = "allow_session"
    CANCEL = "cancel"


@dataclass(slots=True)
class ApprovalRequest:
    id: str
    run_id: str
    session_id: str
    kind: ApprovalKind
    title: str
    payload: dict[str, Any]
    created_at: float

    @classmethod
    def create(
        cls,
        run_id: str,
        session_id: str,
        kind: ApprovalKind,
        title: str,
        payload: dict[str, Any],
    ) -> ApprovalRequest:
        return cls(uuid.uuid4().hex, run_id, session_id, kind, title, payload, time.time())

    def event_data(self) -> dict[str, Any]:
        return {
            "approval_id": self.id,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "kind": self.kind.value,
            "title": self.title,
            "payload": self.payload,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class ApprovalDecision:
    action: ApprovalAction
    feedback: str = ""


Emitter = Callable[[str, dict[str, Any]], None]


class ApprovalGateway(Protocol):
    def request(
        self,
        request: ApprovalRequest,
        emit: Emitter | None = None,
    ) -> ApprovalDecision: ...
