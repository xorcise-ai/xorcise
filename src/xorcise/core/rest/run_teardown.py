"""Release a terminal run's environment (rest layer).

Completing/aborting a run must stop its mission container AND remove its tailnet nodes, or the
tailnet accumulates online subnet routers that later runs collide with. Reached from
run_terminate.grade_and_record — the single point every terminal path (endpoint background tasks
AND the budget watchdog) passes through — and retried by the release sweeper until it succeeds.

The two halves are built and released independently, so one plane being down never skips the
other (#173). The outcome is recorded on the run: `released_at` once both are gone, otherwise the
attempt count and the error, which the sweeper and the run page read. Never raises: a teardown
error must not break grading, and it is no longer lost either."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from xorcise.core import runs
from xorcise.core.config import get_settings
from xorcise.core.rest.run_create import (
    CONTROL_API_KEY,
    build_teardown_control,
    build_teardown_fence,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReleaseOutcome:
    released: bool
    error: str = ""  # one line per failed half; empty when released


def _one_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__


def _release_container(run_id: str) -> None:
    build_teardown_control(get_settings()).teardown(run_id, credential=CONTROL_API_KEY)


def _release_tailnet(run_id: str) -> None:
    build_teardown_fence(get_settings()).teardown_run_network(run_id)


def teardown_run(
    run_id: str, *, now_fn: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> ReleaseOutcome:
    """Stop the run's container and remove its tailnet nodes, and record how that went.

    Idempotent: both underlying teardowns are keyed on the run id and no-op on what is already
    gone, so a retry is always safe. Logs a failure as a warning on the run's FIRST failed attempt
    only; the sweeper's retries log at debug, so a plane that is down for an hour does not flood
    the log, and a release that finally lands after failures says so at info."""
    failures: list[tuple[str, BaseException]] = []
    for half, release in (("container", _release_container), ("tailnet", _release_tailnet)):
        try:
            release(run_id)
        except Exception as exc:  # best-effort per half — never break grading or the other half
            failures.append((half, exc))
    if not failures:
        before = runs.release_state(run_id)
        runs.mark_released(run_id, now_fn())
        if before is not None and before.released_at is None and before.attempts:
            log.info("teardown: released %s after %d failed attempt(s)", run_id, before.attempts)
        return ReleaseOutcome(released=True)
    error = "; ".join(f"{half}: {_one_line(exc)}" for half, exc in failures)
    attempts = runs.record_release_failure(run_id, error)
    for half, failure in failures:
        if attempts <= 1:
            log.warning(
                "teardown: releasing the %s of %s failed (retried automatically)",
                half,
                run_id,
                exc_info=failure,
            )
        else:
            log.debug(
                "teardown: retry %d for the %s of %s failed: %s", attempts, half, run_id, failure
            )
    return ReleaseOutcome(released=False, error=error)


def release_before_delete(run_ids: Sequence[str]) -> list[str]:
    """Release any of these terminal runs whose environment is still up. Returns the ones that
    could not be released, each with its error; the caller must not delete those.

    The run row is the release sweeper's only handle on a run (#173). Deleting it first would leave
    the container until the next boot and the tailnet nodes forever, so a delete tries the release
    here, synchronously, and goes ahead only once it has landed."""
    failed: list[str] = []
    for run_id in run_ids:
        release = runs.release_state(run_id)
        if release is None or release.released_at is not None or not runs.has_environment(run_id):
            continue
        outcome = teardown_run(run_id)
        if not outcome.released:
            failed.append(f"{run_id} ({outcome.error})")
    return failed


def unreleased_detail(failed: Sequence[str]) -> str:
    """The 409 detail for a delete refused by release_before_delete."""
    return (
        "could not tear down the environment of run "
        + ", ".join(failed)
        + ". Deleting it now would leak its container and tailnet nodes. The release is retried "
        "automatically; delete again once it has landed"
    )
