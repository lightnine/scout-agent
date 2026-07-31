"""结构化 Trace。

Agent 出问题时最难受的是"不知道它当时在想什么"。这里把每个事件按 JSONL
落盘，一行一条，事后可以直接 ``jq`` 过滤，也能算出每步耗时和 token 消耗。
JSONL 而不是数据库，是为了让"追加写 + 流式读"这两个最常用的动作都最简单。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from ..core.events import Event, EventType

# 这些事件量大且没有事后分析价值，落盘只会淹没有用的信息
SKIPPED = {EventType.LLM_DELTA}


class TraceRecorder:
    def __init__(self, path: Path, run_id: str = "") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.run_id = run_id
        self._lock = threading.Lock()

    def handle(self, event: Event) -> None:
        if event.type in SKIPPED:
            return
        record = {
            "ts": round(event.ts, 3),
            "run_id": self.run_id,
            "agent": event.agent,
            "type": event.type.value,
            **_compact(event.data),
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")

    def tail(self, limit: int = 30) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").strip().splitlines()
        records = []
        for line in lines[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records


def _compact(data: dict[str, Any], limit: int = 500) -> dict[str, Any]:
    """长文本只留头部——trace 是用来定位问题的，不是用来存档全文的。"""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str) and len(value) > limit:
            out[key] = value[:limit] + f"...[共 {len(value)} 字符]"
        else:
            out[key] = value
    return out
