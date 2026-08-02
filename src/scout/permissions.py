"""权限与审批。

Agent 真正的风险不是"想错了"，而是"想错了还真去执行"。
这一层把「模型决定做什么」和「系统允许做什么」分开，是能上生产的前提。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .approval import (
    ApprovalAction,
    ApprovalGateway,
    ApprovalKind,
    ApprovalRequest,
    Emitter,
)
from .cancellation import RunCancelled
from .tools.base import Risk, Tool


@dataclass(slots=True)
class Decision:
    allowed: bool
    reason: str = ""


class Approver(Protocol):
    def check(
        self,
        tool: Tool,
        args: dict[str, Any],
        *,
        session_id: str,
        run_id: str,
    ) -> Decision: ...


class PolicyApprover:
    """基于策略 + 交互回调的审批器。

    - ``auto``     全部放行（无人值守场景，建议配合容器沙箱）
    - ``ask``      CAUTION 及以上交给回调询问用户，用户可以选择"本次会话都同意"
    - ``readonly`` 只允许 SAFE 工具，任何落盘/写操作都拒绝
    """

    def __init__(
        self,
        mode: str = "ask",
        gateway: ApprovalGateway | None = None,
        emit: Emitter | None = None,
    ) -> None:
        self.mode = mode
        self.gateway = gateway
        self.emit = emit
        self._session_allow: dict[str, set[str]] = {}

    def check(
        self,
        tool: Tool,
        args: dict[str, Any],
        *,
        session_id: str,
        run_id: str,
    ) -> Decision:
        if tool.risk == Risk.SAFE:
            return Decision(True)
        if self.mode == "readonly":
            return Decision(False, f"当前是只读模式，{tool.name} 会产生副作用，已拒绝")
        if self.mode == "auto" or tool.name in self._session_allow.get(session_id, set()):
            return Decision(True)
        if self.gateway is None:
            return Decision(False, f"当前无交互审批通道，拒绝执行 {tool.name}")

        request = ApprovalRequest.create(
            run_id,
            session_id,
            ApprovalKind.TOOL,
            f"执行工具 {tool.name}",
            {"tool": tool.name, "arguments": args, "risk": int(tool.risk)},
        )
        decision = self.gateway.request(request, self.emit)
        if decision.action is ApprovalAction.CANCEL:
            raise RunCancelled("用户取消运行")
        if decision.action is ApprovalAction.ALLOW_SESSION:
            self._session_allow.setdefault(session_id, set()).add(tool.name)
            return Decision(True)
        if decision.action is ApprovalAction.APPROVE:
            return Decision(True)
        reason = decision.feedback or f"用户拒绝执行 {tool.name}，请换一种方式或询问用户"
        return Decision(False, reason)

    def clear_session(self, session_id: str) -> None:
        self._session_allow.pop(session_id, None)
