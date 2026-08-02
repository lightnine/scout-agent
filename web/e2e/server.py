"""Deterministic Scout application used by Playwright end-to-end tests."""

from __future__ import annotations

import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scout.config import Settings
from scout.core.session import Session
from scout.llm.base import LLMResponse, Message, ToolCall, Usage
from scout.runtime import Runtime
from scout.web.app import create_app

COMPLETED_QUESTION = "Compare SQLite and DuckDB"
CANCELLED_QUESTION = "Wait until stopped"
PARTIAL_REPORT = "Deterministic report is streaming"
FINAL_REPORT = f"{PARTIAL_REPORT} and then persisted [S1]."
_stream_release = threading.Event()


class E2ELLM:
    """Return a deterministic tool sequence without model or public-network calls."""

    model = "e2e"
    embedding_model = ""

    def chat(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]] | None = None,
        *,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
        **_: Any,
    ) -> LLMResponse:
        del tools
        question = next(
            (
                message.content
                for message in messages
                if message.role == "user"
                and message.content in {COMPLETED_QUESTION, CANCELLED_QUESTION}
            ),
            "",
        )
        called_tools = {
            call.name
            for message in messages
            if message.role == "assistant"
            for call in message.tool_calls
        }

        if "update_plan" not in called_tools:
            message = Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id=f"plan-{question}",
                        name="update_plan",
                        arguments={
                            "steps": ["Confirm the research scope", "Produce a cited answer"],
                            "current": 1,
                        },
                    )
                ],
            )
        elif question == CANCELLED_QUESTION:
            time.sleep(2)
            message = Message(
                role="assistant",
                tool_calls=[ToolCall(id="after-cancel", name="list_sources", arguments={})],
            )
        elif "http_request" not in called_tools:
            message = Message(
                role="assistant",
                tool_calls=[
                    ToolCall(
                        id="local-health",
                        name="http_request",
                        arguments={"url": "http://127.0.0.1:8000/api/health"},
                    )
                ],
            )
        else:
            _stream_release.clear()
            if stream and on_delta:
                on_delta(PARTIAL_REPORT)
                if not _stream_release.wait(timeout=15):
                    raise TimeoutError("E2E stream release was not received")
                on_delta(" and then persisted [S1].")
            message = Message(role="assistant", content=FINAL_REPORT)

        return LLMResponse(
            message=message,
            usage=Usage(prompt_tokens=10, completion_tokens=5, calls=1),
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class E2ERuntime(Runtime):
    """Seed one local evidence record so source rendering is deterministic."""

    def new_session(self, title: str = "") -> Session:
        session = super().new_session(title)
        session.evidence.ingest(
            "https://example.test/scout-e2e",
            "Deterministic E2E Source",
            "SQLite and DuckDB are embedded analytical systems used by this deterministic test.",
        )
        return session


_workspace = tempfile.TemporaryDirectory(prefix="scout-web-e2e-")
workspace = Path(_workspace.name)
settings = Settings(
    api_key="e2e",
    model="e2e",
    workspace=workspace,
    home=workspace / ".scout",
    permission_mode="ask",
    max_steps=6,
)
settings.ensure_dirs()
runtime = E2ERuntime(settings, llm=E2ELLM(), enable_trace=False)
app = create_app(settings=settings, runtime=runtime)


@app.post("/api/e2e/release-stream")
def release_stream() -> dict[str, str]:
    _stream_release.set()
    return {"status": "released"}


# create_app mounts the production SPA last. This test-only route must precede
# that catch-all mount and is intentionally defined only in this fixture.
app.router.routes.insert(0, app.router.routes.pop())
