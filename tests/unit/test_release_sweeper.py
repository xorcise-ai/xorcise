"""#173: a terminal run whose environment a teardown failed to release is retried until it lands.

Before, a release was attempted exactly once, in grade_and_record's `finally`. A down Docker or
Headscale, or one failed call, left the container until the next boot and the tailnet nodes
forever, because every other scan covers non-terminal runs only."""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest

from xorcise.core import runs
from xorcise.core.rest import release_sweeper
from xorcise.core.rest.release_sweeper import ReleaseSweeper, grading_settled
from xorcise.core.runs import AwaitingRelease

pytestmark = pytest.mark.unit

T0 = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def _item(run_id: str = "r1", *, trigger: str = "done", ended: datetime = T0) -> AwaitingRelease:
    return AwaitingRelease(run_id, trigger, ended, 0, None)


def _recording(into: list[str]):
    """A release that always lands and records the run it was asked to release."""

    def release(run_id: str) -> bool:
        into.append(run_id)
        return True

    return release


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _sweeper(
    awaiting: list[AwaitingRelease],
    release,
    *,
    clock: _Clock | None = None,
    settled=lambda item, now: True,
    batch: int = 20,
) -> ReleaseSweeper:
    return ReleaseSweeper(
        release=release,
        list_awaiting=lambda: list(awaiting),
        settled=settled,
        now_fn=clock or _Clock(),
        interval=1,
        batch=batch,
    )


# --- the sweep -----------------------------------------------------------------------------------


def test_releases_every_settled_run() -> None:
    released: list[str] = []
    sweeper = _sweeper([_item("a"), _item("b")], _recording(released))
    assert sweeper.tick() == 2
    assert released == ["a", "b"]


def test_leaves_a_run_whose_grading_has_not_settled() -> None:
    released: list[str] = []
    sweeper = _sweeper(
        [_item("grading"), _item("done")],
        _recording(released),
        settled=lambda item, now: item.run_id != "grading",
    )
    assert sweeper.tick() == 1
    assert released == ["done"]


def test_attempts_at_most_a_batch_per_scan() -> None:
    attempted: list[str] = []
    backlog = [_item(f"r{i}") for i in range(5)]
    sweeper = _sweeper(backlog, _recording(attempted), batch=2)
    sweeper.tick()
    assert attempted == ["r0", "r1"]  # oldest first, as listed


def test_a_failed_release_backs_off_and_doubles() -> None:
    clock = _Clock()
    attempts: list[datetime] = []

    def release(rid: str) -> bool:
        attempts.append(clock.now)
        return False

    sweeper = _sweeper([_item()], release, clock=clock)
    sweeper.tick()  # fails: next try in 30 s
    clock.now = T0 + timedelta(seconds=29)
    sweeper.tick()  # not due
    clock.now = T0 + timedelta(seconds=30)
    sweeper.tick()  # fails again: next try in 60 s
    clock.now = T0 + timedelta(seconds=89)
    sweeper.tick()  # not due
    clock.now = T0 + timedelta(seconds=90)
    sweeper.tick()
    assert attempts == [T0, T0 + timedelta(seconds=30), T0 + timedelta(seconds=90)]


def test_the_backoff_is_capped_at_ten_minutes() -> None:
    clock = _Clock()
    count = 0

    def release(rid: str) -> bool:
        nonlocal count
        count += 1
        return False

    sweeper = _sweeper([_item()], release, clock=clock)
    for _ in range(8):  # 30 s doubling would be 64 min by the eighth failure
        sweeper.tick()
        clock.now += timedelta(minutes=10)
    assert count == 8


def test_a_run_in_backoff_does_not_take_a_batch_slot() -> None:
    clock = _Clock()
    attempted: list[str] = []

    def release(rid: str) -> bool:
        attempted.append(rid)
        return rid != "stuck"

    backlog = [_item("stuck"), _item("next")]
    sweeper = _sweeper(backlog, release, clock=clock, batch=1)
    sweeper.tick()  # "stuck" fails and backs off
    sweeper.tick()  # "stuck" is not due, so "next" gets the slot
    assert attempted == ["stuck", "next"]


def test_a_raising_release_does_not_stop_the_scan() -> None:
    released: list[str] = []

    def release(rid: str) -> bool:
        if rid == "a":
            raise RuntimeError("database is locked")
        released.append(rid)
        return True

    sweeper = _sweeper([_item("a"), _item("b")], release)
    assert sweeper.tick() == 1
    assert released == ["b"]


def test_overdue_releases_are_summarised_once_an_hour_at_error(caplog) -> None:
    # Review of #185: one ERROR per run fired at once for every historic run while a plane was
    # down. Now one summary line, at most hourly, naming the count and the oldest.
    clock = _Clock()
    backlog = [
        AwaitingRelease("old", "done", T0, 4, "tailnet: Headscale is not reachable"),
        AwaitingRelease("new", "done", T0 + timedelta(minutes=5), 1, "tailnet: same"),
        AwaitingRelease("fresh", "done", T0 + timedelta(hours=2), 0, None),  # not overdue
    ]
    sweeper = _sweeper(backlog, lambda rid: False, clock=clock)
    with caplog.at_level("ERROR", logger=release_sweeper.__name__):
        clock.now = T0 + timedelta(minutes=59)
        sweeper.tick()
        assert not caplog.records  # nothing has waited an hour yet
        clock.now = T0 + timedelta(hours=2)
        sweeper.tick()
        clock.now += timedelta(minutes=30)
        sweeper.tick()  # within the hour: quiet
        clock.now += timedelta(minutes=31)
        sweeper.tick()  # an hour on: said again
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 2
    assert errors[0].startswith("2 finished run(s) still hold their container or tailnet nodes")
    assert "Oldest: run old, ended 120 min ago (tailnet: Headscale is not reachable)" in errors[0]


def test_a_settled_check_that_raises_does_not_stop_the_scan() -> None:
    # Review of #185: the settled check reads the run's stored result, outside the per-run try.
    # One row failing validation raised out of every scan, and no later run was ever released.
    released: list[str] = []

    def settled(item: AwaitingRelease, now: datetime) -> bool:
        if item.run_id == "broken":
            raise ValueError("1 validation error for GradeResult")
        return True

    sweeper = _sweeper([_item("broken"), _item("next")], _recording(released), settled=settled)
    assert sweeper.tick() == 1
    assert released == ["next"]
    assert "broken" in sweeper._next_at  # backed off like any failure, not retried every scan


def test_forgets_runs_that_left_the_backlog() -> None:
    backlog = [_item("a")]
    sweeper = _sweeper(backlog, lambda rid: False)
    sweeper.tick()
    assert "a" in sweeper._next_at
    backlog.clear()  # released elsewhere, or deleted
    sweeper.tick()
    assert sweeper._next_at == {} and sweeper._failures == {}


def test_a_failing_scan_keeps_the_sweeper_running() -> None:
    calls = 0
    released = threading.Event()

    def list_awaiting() -> list[AwaitingRelease]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database is locked")
        return [_item()]

    def release(run_id: str) -> bool:
        released.set()
        return True

    sweeper = ReleaseSweeper(
        release=release,
        list_awaiting=list_awaiting,
        settled=lambda item, now: True,
        now_fn=lambda: T0,
        interval=0.01,
        batch=20,
    )

    async def scenario() -> None:
        sweeper.start()
        try:
            for _ in range(200):
                if released.is_set():
                    break
                await asyncio.sleep(0.01)
        finally:
            await sweeper.stop()

    asyncio.run(scenario())
    assert released.is_set(), "the sweeper died after its first failed scan"


# --- when a run's grading has settled -----------------------------------------------------------


def test_a_run_being_graded_is_never_settled(monkeypatch) -> None:
    from xorcise.core.rest import run_terminate

    monkeypatch.setattr(run_terminate, "_grading_in_flight", {"r1"})
    assert (
        grading_settled(_item("r1", trigger="crashed", ended=T0 - timedelta(hours=1)), T0) is False
    )


def test_an_ungraded_run_settles_after_the_drain_and_a_grace(monkeypatch) -> None:
    from xorcise.core.config import get_settings

    monkeypatch.setenv("XORCISE_TELEMETRY_DRAIN_SECONDS", "5")
    get_settings.cache_clear()
    item = _item(trigger="deploy_failed", ended=T0)
    assert grading_settled(item, T0 + timedelta(seconds=34)) is False
    assert grading_settled(item, T0 + timedelta(seconds=35)) is True


def test_a_graded_trigger_settles_only_once_its_result_is_recorded(monkeypatch) -> None:
    from xorcise.core import reporting

    results: dict[str, object] = {}
    monkeypatch.setattr(reporting, "get_result", lambda rid: results.get(rid))
    item = _item(trigger="timeout", ended=T0 - timedelta(hours=1))
    # Grading may still need the environment: a run whose grade was lost waits for the grade.
    assert grading_settled(item, T0) is False
    results["r1"] = object()
    assert grading_settled(item, T0) is True


# --- the repository side ------------------------------------------------------------------------


_subnets = iter(range(1, 250))


def _lab(run_id: str) -> None:
    cidr = f"10.200.{next(_subnets)}.0/24"  # network_cidr is unique across every row
    runs.reserve_run(run_id, "a1", "c", network_cidr=cidr, entry_cidrs=cidr)
    runs.finalize_run(run_id, budget_seconds=300, prompt="P")


def test_awaiting_lists_unreleased_terminal_lab_runs_oldest_first(migrated_home) -> None:
    for rid in ("live", "late", "early", "done"):
        _lab(rid)
    runs.reserve_run("static", "a1", "c", network_cidr="", entry_cidrs="")
    runs.finalize_run("static", budget_seconds=300, prompt="P")
    runs.mark_terminal("late", "done", T0 + timedelta(minutes=5))
    runs.mark_terminal("early", "timeout", T0)
    runs.mark_terminal("done", "done", T0)
    runs.mark_released("done", T0)
    runs.mark_terminal("static", "done", T0)  # owns nothing to release
    awaiting = runs.terminal_runs_awaiting_release()
    assert [a.run_id for a in awaiting] == ["early", "late"]
    assert awaiting[0].trigger == "timeout"
    assert awaiting[0].completed_at == T0


def test_mark_released_is_first_wins(migrated_home) -> None:
    _lab("r1")
    runs.mark_terminal("r1", "done", T0)
    runs.mark_released("r1", T0)
    runs.mark_released("r1", T0 + timedelta(minutes=1))
    state = runs.release_state("r1")
    assert state is not None and state.released_at == T0


def test_a_failure_is_not_counted_against_a_released_or_missing_run(migrated_home) -> None:
    _lab("r1")
    runs.mark_terminal("r1", "done", T0)
    assert runs.record_release_failure("r1", "boom") == 1
    assert runs.record_release_failure("r1", "boom " * 200) == 2
    state = runs.release_state("r1")
    assert state is not None and state.error is not None and len(state.error) == 500
    runs.mark_released("r1", T0)
    assert runs.record_release_failure("r1", "late") == 0
    assert runs.record_release_failure("ghost", "boom") == 0
    assert runs.release_state("ghost") is None
