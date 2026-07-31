"""权限与审批。

Agent 真正的风险不是"想错了"，而是"想错了还真去执行"。
这一层把「模型决定做什么」和「系统允许做什么」分开，是能上生产的前提。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .tools.base import Risk, Tool


@dataclass(slots=True)
class Decision:
    allowed: bool
    reason: str = ""


class Approver(Protocol):
    def check(self, tool: Tool, args: dict[str, Any]) -> Decision: ...


class PolicyApprover:
    """基于策略 + 交互回调的审批器。

    - ``auto``     全部放行（无人值守场景，建议配合容器沙箱）
    - ``ask``      CAUTION 及以上交给回调询问用户，用户可以选择"本次会话都同意"
    - ``readonly`` 只允许 SAFE 工具，任何落盘/写操作都拒绝
    """

    def __init__(
        self,
        mode: str = "ask",
        prompt: Callable[[Tool, dict[str, Any]], bool] | None = None,
    ) -> None:
        self.mode = mode
        self.prompt = prompt
        self._always_allow: set[str] = set()

    def check(self, tool: Tool, args: dict[str, Any]) -> Decision:
        if tool.risk == Risk.SAFE:
            return Decision(True)
        if self.mode == "readonly":
            return Decision(False, f"当前是只读模式，{tool.name} 会产生副作用，已拒绝")
        if self.mode == "auto" or tool.name in self._always_allow:
            return Decision(True)
        if self.prompt is None:  # 非交互环境（如单测）默认放行
            return Decision(True)
        if self.prompt(tool, args):
            return Decision(True)
        return Decision(False, f"用户拒绝执行 {tool.name}，请换一种方式或询问用户")

    def always_allow(self, tool_name: str) -> None:
        self._always_allow.add(tool_name)
