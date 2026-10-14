"""Retry releasing terminal runs whose environment is still up (rest layer, #173).

A run's container and tailnet nodes are released once, in grade_and_record's `finally`. When that
fails (Docker or Headscale down, one failed call), nothing else ever revisited the run: every
other scan covers non-terminal runs only, and the boot orphan sweep covers Docker alone. This scan
retries every terminal run whose `released_at` is still empty until the release lands.

It only takes a run whose grading is settled: one with a recorded result, or one that ended on
an environment failure (never graded) once its telemetry drain is over. Grading may still need the
environment, and a grade in flight releases the run itself when it finishes. A run whose grade was
lost waits for the grade to be re-driven (a result poll, or the boot regrade), which releases it.

Each run backs off on its own (30 s doubling to 10 min) so a down plane is not hammered, and a scan
attempts at most `batch` runs. A run still unreleased an hour after it ended is logged once at
error level. It never gives up."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from xorcise.core.config import get_settings
from xorcise.core.contracts.run import UNGRADED_TRIGGERS
from xorcise.core.runs import AwaitingRelease

log = logging.getLogger(__name__)

_BACKOFF_BASE = timedelta(seconds=30)
_BACKOFF_MAX = timedelta(minutes=10)
_ESCALATE_AFTER = timedelta(hours=1)


class ReleaseSweeper:
    def __init__(
        self,
        release: Callable[[str], bool],
        list_awaiting: Callable[[], list[AwaitingRelease]],
        settled: Callable[[AwaitingRelease, datetime], bool],
        now_fn: Callable[[], datetime],
        interval: float,
        batch: int,
    ) -> None:
        self._release = release
        self._list_awaiting = list_awaiting
        self._settled = settled
        self._now = now_fn
        self._interval = interval
        self._batch = batch
        self._failures: dict[str, int] = {}
        self._next_at: dict[str, datetime] = {}
        self._escalated: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    def tick(self) -> int:
        """One scan. Returns how many runs it released."""
        now = self._now()
        awaiting = self._list_awaiting()
        # Forget runs that left the backlog (released elsewhere, or deleted), so the maps stay
        # bounded over a long-lived process.
        live = {a.run_id for a in awaiting}
        self._failures = {k: v for k, v in self._failures.items() if k in live}
        self._next_at = {k: v for k, v in self._next_at.items() if k in live}
        self._escalated &= live
        released = attempted = 0
        for item in awaiting:
            if attempted >= self._batch:
                break
            due = self._next_at.get(item.run_id)
            if due is not None and now < due:
                continue
            if not self._settled(item, now):
                continue
            attempted += 1
            # Isolate each run: one failure must not abort the scan (teardown_run itself never
            # raises, but a DB error recording its outcome could).
            try:
                ok = self._release(item.run_id)
            except Exception:
                log.warning("release sweep: releasing %s failed", item.run_id, exc_info=True)
                ok = False
            if ok:
                released += 1
                self._failures.pop(item.run_id, None)
                self._next_at.pop(item.run_id, None)
                continue
            failures = self._failures[item.run_id] = self._failures.get(item.run_id, 0) + 1
            self._next_at[item.run_id] = now + min(
                _BACKOFF_BASE * 2 ** (failures - 1), _BACKOFF_MAX
            )
            self._maybe_escalate(item, now)
        return released

    def _maybe_escalate(self, item: AwaitingRelease, now: datetime) -> None:
        ended = item.completed_at
        if ended is None or item.run_id in self._escalated or now - ended < _ESCALATE_AFTER:
            return
        self._escalated.add(item.run_id)
        log.error(
            "the environment of run %s is still not released %d min after it ended (%s); "
            "its container or tailnet nodes are still up, and the release keeps being retried",
            item.run_id,
            int((now - ended).total_seconds() // 60),
            item.error or "no error recorded",
        )

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            # Worker thread: a release does Docker and Headscale I/O, which must never block the
            # event loop REST and OTLP are served from.
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                # Listing the backlog failed. The next scan retries, and the task never dies.
                log.warning("release sweep: scan failed (retried next interval)", exc_info=True)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None


# Beyond the drain window before an ungraded run counts as settled: room for the seal and the
# events export that grade_and_record runs after the drain.
_SETTLE_GRACE = timedelta(seconds=30)


def grading_settled(item: AwaitingRelease, now: datetime) -> bool:
    """True when releasing this run's environment can no longer pull it out from under its grade."""
    # Lazy: run_terminate pulls the grading path (otel, eval) — keep it off this module's import.
    from xorcise.core import reporting
    from xorcise.core.rest.run_terminate import is_grading

    if is_grading(item.run_id):
        return False  # its own teardown runs when the grade finishes
    if item.trigger in UNGRADED_TRIGGERS:
        drain = timedelta(seconds=get_settings().telemetry_drain_seconds)
        return item.completed_at is not None and now >= item.completed_at + drain + _SETTLE_GRACE
    return reporting.get_result(item.run_id) is not None


def release_run(run_id: str) -> bool:
    """Release one run's environment and say whether it is now released."""
    from xorcise.core.rest.run_teardown import teardown_run

    return teardown_run(run_id).released
