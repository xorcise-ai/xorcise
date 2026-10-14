import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest

from xorcise.core.rest.budget_watchdog import BudgetWatchdog

pytestmark = pytest.mark.unit


def test_tick_terminates_only_runs_past_deadline() -> None:
    now = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    active = [("expired", now - timedelta(seconds=1)), ("live", now + timedelta(seconds=60))]
    fired: list[tuple[str, str]] = []
    wd = BudgetWatchdog(
        terminate=lambda rid, trig, n: fired.append((rid, trig)),
        list_active=lambda: active,
        now_fn=lambda: now,
        interval=1,
    )
    assert wd.tick() == 1
    assert fired == [("expired", "timeout")]


def test_tick_is_a_noop_when_nothing_expired() -> None:
    now = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    wd = BudgetWatchdog(
        terminate=lambda *a: pytest.fail("should not fire"),
        list_active=lambda: [("live", now + timedelta(seconds=10))],
        now_fn=lambda: now,
        interval=1,
    )
    assert wd.tick() == 0


def test_a_slow_terminate_does_not_block_the_event_loop() -> None:
    """#166: terminate grades in the same call (drain sleep, judge, teardown), and REST and OTLP
    are served from this loop. The tick must run off-loop, or neither answers until it ends."""
    now = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_terminate(rid: str, trig: str, n: datetime) -> None:
        entered.set()
        # A bounded wait, so a regression fails the test below instead of hanging the suite.
        release.wait(timeout=5)
        finished.set()

    wd = BudgetWatchdog(
        terminate=slow_terminate,
        list_active=lambda: [("expired", now - timedelta(seconds=1))],
        now_fn=lambda: now,
        interval=0.01,
    )

    async def scenario() -> None:
        wd.start()
        try:
            # Polled with asyncio.sleep: if the tick held the loop, this coroutine could not
            # resume until terminate returned, after `release` times out.
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set(), "the watchdog never ticked"
            # The loop still serves other work while terminate is inside its wait.
            await asyncio.wait_for(asyncio.sleep(0.05), timeout=1)
            assert not finished.is_set(), "the loop only resumed once terminate had returned"
        finally:
            release.set()
            await wd.stop()

    asyncio.run(scenario())


def test_one_failed_terminate_does_not_stop_the_others(caplog: pytest.LogCaptureFixture) -> None:
    """A terminate that raises (e.g. `database is locked`) must not abort the scan: the other
    expired runs still close, and the failure is logged for the next tick to retry."""
    now = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    expired = now - timedelta(seconds=1)
    fired: list[str] = []

    def terminate(rid: str, trig: str, n: datetime) -> None:
        if rid == "a":
            raise RuntimeError("database is locked")
        fired.append(rid)

    wd = BudgetWatchdog(
        terminate=terminate,
        list_active=lambda: [("a", expired), ("b", expired)],
        now_fn=lambda: now,
        interval=1,
    )
    with caplog.at_level("WARNING", logger="xorcise.core.rest.budget_watchdog"):
        assert wd.tick() == 1
    assert fired == ["b"]
    assert "terminate failed for a" in caplog.text


def test_a_failing_scan_keeps_the_watchdog_running() -> None:
    """A scan that raises must not end run(): later ticks still terminate, and stop() does not
    re-raise the stored error into the shutdown hooks that follow it."""
    now = datetime(2026, 6, 25, 12, 0, tzinfo=UTC)
    calls = 0
    fired = threading.Event()

    def list_active() -> list[tuple[str, datetime]]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is locked")
        return [("a", now - timedelta(seconds=1))]

    wd = BudgetWatchdog(
        terminate=lambda *a: fired.set(),
        list_active=list_active,
        now_fn=lambda: now,
        interval=0.01,
    )

    async def scenario() -> None:
        wd.start()
        try:
            for _ in range(200):
                if fired.is_set():
                    break
                await asyncio.sleep(0.01)
        finally:
            await wd.stop()

    asyncio.run(scenario())
    assert fired.is_set(), "the watchdog died after its first failed scan"
