from __future__ import annotations

from pathlib import Path

import pytest

from scout.approval import ApprovalDecision
from scout.config import Settings
from scout.llm.base import LLMResponse, Message, ToolCall, Usage
from scout.memory.store import Store


class FakeLLM:
    """按剧本回复的假模型，让整个 Agent Loop 可以离线、确定性地测试。"""

    model = "fake-model"
    embedding_model = ""

    def __init__(self, script: list[Message] | None = None) -> None:
        self.script = list(script or [])
        self.received: list[list[Message]] = []
        self.tools_seen: list[list[dict] | None] = []

    def chat(
        self,
        messages,
        tools=None,
        *,
        stream=False,
        on_delta=None,
        temperature=None,
        model=None,
    ) -> LLMResponse:
        self.received.append(list(messages))
        self.tools_seen.append(tools)
        message = self.script.pop(0) if self.script else Message(role="assistant", content="完毕")
        if stream and on_delta and message.content:
            on_delta(message.content)
        return LLMResponse(message=message, usage=Usage(prompt_tokens=10, completion_tokens=5))

    def embed(self, texts):
        return []


class ScriptedGateway:
    def __init__(self, decisions: list[ApprovalDecision]):
        self.decisions = list(decisions)
        self.requests = []

    def request(self, request, emit=None):
        self.requests.append(request)
        if emit:
            emit("approval_required", request.event_data())
        decision = self.decisions.pop(0)
        if emit:
            emit(
                "approval_resolved",
                {
                    "approval_id": request.id,
                    "run_id": request.run_id,
                    "session_id": request.session_id,
                    "action": decision.action.value,
                },
            )
        return decision


def assistant_tool_call(name: str, arguments: dict, call_id: str = "c1") -> Message:
    return Message(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments)],
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.md").write_text("向量数据库调研笔记\n第二行", encoding="utf-8")
    return tmp_path


@pytest.fixture
def settings(workspace: Path) -> Settings:
    s = Settings(
        api_key="test-key",
        model="fake-model",
        workspace=workspace,
        home=workspace / ".scout",
        max_steps=5,
        subagent_max_steps=3,
        max_subagents=2,
        permission_mode="auto",
    )
    s.ensure_dirs()
    return s


@pytest.fixture
def store(settings: Settings) -> Store:
    s = Store(settings.db_path)
    yield s
    s.close()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()
