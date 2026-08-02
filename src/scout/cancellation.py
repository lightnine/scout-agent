from __future__ import annotations

import threading


class RunCancelled(RuntimeError):
    pass


class RunCancellation:
    def __init__(self) -> None:
        self._event = threading.Event()

    def request(self) -> None:
        self._event.set()

    def clear(self) -> None:
        self._event.clear()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def ensure_active(self) -> None:
        if self.is_cancelled():
            raise RunCancelled("运行已取消")
