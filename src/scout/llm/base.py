"""模型抽象层。

框架内部只认识这里定义的 ``Message`` / ``ToolCall``，各家 API 的报文差异
由具体 client 负责翻译。换模型供应商 = 新增一个 client 实现，上层零改动。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

Role = Literal["system", "user", "assistant", "tool"]


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。

    不引入 tiktoken：不同厂商分词器本就不同，装了也只是"另一种不准"。
    这里按 CJK 字符约 1 token、其余约 4 字符 1 token 估算，误差 ±15%，
    对"要不要触发上下文压缩"这类阈值判断完全够用。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk) // 4 + 1


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": self.raw_arguments
                or json.dumps(self.arguments, ensure_ascii=False),
            },
        }


@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai(self) -> dict[str, Any]:
        data: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [tc.to_openai() for tc in self.tool_calls]
            # 带 tool_calls 的 assistant 消息，多数服务端要求 content 可为 null
            data["content"] = self.content or None
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        if self.name and self.role == "tool":
            data["name"] = self.name
        return data

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in self.tool_calls
            ],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        return cls(
            role=data["role"],
            content=data.get("content") or "",
            tool_calls=[
                ToolCall(id=tc["id"], name=tc["name"], arguments=tc.get("arguments") or {})
                for tc in data.get("tool_calls") or []
            ],
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
        )

    def token_estimate(self) -> int:
        total = estimate_tokens(self.content or "")
        for tc in self.tool_calls:
            total += estimate_tokens(tc.name + json.dumps(tc.arguments, ensure_ascii=False))
        return total + 4  # 每条消息的协议固定开销


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.cached_tokens + other.cached_tokens,
            self.calls + other.calls,
        )


@dataclass(slots=True)
class LLMResponse:
    message: Message
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"
    latency_ms: int = 0


DeltaHandler = Callable[[str], None]


@runtime_checkable
class LLMClient(Protocol):
    """模型客户端接口。测试里的 FakeLLM 也实现这个协议。"""

    model: str

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
        on_delta: DeltaHandler | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> LLMResponse: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...
