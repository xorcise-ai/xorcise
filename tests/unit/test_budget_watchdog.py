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
