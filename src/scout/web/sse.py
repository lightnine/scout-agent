"""Server-sent event formatting and replay."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any


def encode_sse(envelope: Any) -> str:
    """Encode a run event using the SSE wire format."""
    payload = json.dumps(envelope.to_dict(), ensure_ascii=False, default=str)
    return f"id: {envelope.id}\nevent: {envelope.type}\ndata: {payload}\n\n"


def stream_events(manager: Any, run_id: str, after_id: int = 0) -> Iterator[str]:
    """Replay retained events, then wait for new ones until completion."""
    cursor = after_id
    while True:
        events = manager.events_after(run_id, cursor)
        for event in events:
            cursor = event.id
            yield encode_sse(event)
        record = manager.get(run_id)
        if record.status == "finished" and not manager.events_after(run_id, cursor):
            break
        if not manager.wait_for_events(run_id, cursor, timeout=15):
            yield ": keep-alive\n\n"
