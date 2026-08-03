"""Background budget watchdog (rest layer).

A periodic scan that terminates runs past their budget deadline with NO traffic — distinct
from the on-access gate backstop. terminate() is the idempotent terminate_run coordinator,
so a scan racing an earlier done/flag is a no-op. Lives in the rest layer (it drives the
seal+record coordinator); the runs module never imports otel/reporting."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from datetime import datetime


class BudgetWatchdog:
    def __init__(
        self,
        terminate: Callable[[str, str, datetime], object],
        list_active: Callable[[], list[tuple[str, datetime]]],
        now_fn: Callable[[], datetime],
        interval: float,
    ) -> None:
        self._terminate = terminate
        self._list_active = list_active
        self._now = now_fn
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def tick(self) -> int:
        now = self._now()
        fired = 0
        for run_id, deadline in self._list_active():
            if now >= deadline:
                self._terminate(run_id, "timeout", now)
                fired += 1
        return fired

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            self.tick()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
