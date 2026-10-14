"""Background budget watchdog (rest layer).

A periodic scan that terminates runs past their budget deadline with NO traffic — distinct
from the on-access gate backstop. terminate() is the idempotent terminate_run coordinator,
so a scan racing an earlier done/flag is a no-op. Lives in the rest layer (it drives the
seal+record coordinator); the runs module never imports otel/reporting."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime

from xorcise.core.contracts.run import TerminalTrigger

log = logging.getLogger(__name__)


class BudgetWatchdog:
    def __init__(
        self,
        terminate: Callable[[str, TerminalTrigger, datetime], object],
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
                # Isolate each run: one failed terminate must not abort the scan, or escape and
                # end run() for the life of the process, after which no idle run ever times out.
                # The run stays active, so the next tick retries it, as the readiness gate does.
                try:
                    self._terminate(run_id, "timeout", now)
                    fired += 1
                except Exception:
                    log.warning(
                        "budget: terminate failed for %s (retried next tick)", run_id, exc_info=True
                    )
        return fired

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            # Worker thread: terminate grades in the same call (the telemetry drain sleep, the
            # judge, Docker teardown), and REST and OTLP share this loop - on it, neither would
            # answer until the grade finished.
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                # The scan itself failed (listing the active runs). Same rule: the next tick
                # retries, and the task never dies.
                log.warning("budget: scan failed (retried next tick)", exc_info=True)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
