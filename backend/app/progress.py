"""Async progress channel used by the SSE endpoints.

The orchestrator runs in a worker thread and calls ``channel.emit(stage, detail)``.
The HTTP handler consumes events from an ``asyncio.Queue`` and writes them
as SSE messages. The channel emits a terminal event (``__done__`` or
``__error__``) so the client knows when to close.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)


class ProgressChannel:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def emit(self, stage: str, detail: dict[str, Any]) -> None:
        """Thread-safe: called from worker threads via ``asyncio.run_coroutine_threadsafe``."""
        msg = {
            "ts": round(time.time() * 1000),
            "id": uuid.uuid4().hex[:8],
            "stage": stage,
            "detail": detail,
        }
        if self.loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.queue.put(msg), self.loop)
        except RuntimeError as e:
            log.debug("progress emit failed: %s", e)

    async def stream(self):
        """Async generator yielding SSE-formatted lines."""
        while True:
            msg = await self.queue.get()
            yield {
                "event": msg.get("stage", "stage"),
                "data": json.dumps(msg, default=str),
            }
            if msg.get("stage") in ("__done__", "__error__"):
                return
