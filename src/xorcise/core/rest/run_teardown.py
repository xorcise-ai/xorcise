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
from xorcise.core.contracts.run import DeleteRefusal, DeleteRefusalReason
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
    the log, and a release that finally lands after failures says so at info.

    Never raises, the bookkeeping included: grade_and_record calls this from a `finally`, and a
    database error escaping there would skip the rest of finalisation (#173)."""
    failures: list[tuple[str, BaseException]] = []
    for half, release in (("container", _release_container), ("tailnet", _release_tailnet)):
        try:
            release(run_id)
        except Exception as exc:  # best-effort per half — never break grading or the other half
            failures.append((half, exc))
    if not failures:
        _record_released(run_id, now_fn())
        return ReleaseOutcome(released=True)
    error = "; ".join(f"{half}: {_one_line(exc)}" for half, exc in failures)
    try:
        attempts = runs.record_release_failure(run_id, error)
        if attempts == 0 and _released_meanwhile(run_id):
            # Another caller (the sweeper, the grade's own teardown, a delete) released the run
            # while this one failed. The environment is gone: reporting a failure would back the
            # sweeper off, or refuse a delete, over a run that is in fact released.
            return ReleaseOutcome(released=True)
    except Exception:
        log.warning("teardown: could not record the failed release of %s", run_id, exc_info=True)
        attempts = 1  # unknown: log it as a first failure rather than hide it
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


def _record_released(run_id: str, at: datetime) -> None:
    """Mark the run released. A database error is logged, not raised: the environment IS gone,
    and the sweeper's next idempotent retry records it."""
    try:
        before = runs.release_state(run_id)
        runs.mark_released(run_id, at)
    except Exception:
        log.warning("teardown: released %s but could not record it", run_id, exc_info=True)
        return
    if before is not None and before.released_at is None and before.attempts:
        log.info("teardown: released %s after %d failed attempt(s)", run_id, before.attempts)


def _released_meanwhile(run_id: str) -> bool:
    state = runs.release_state(run_id)
    return state is not None and state.released_at is not None


# --- deleting a run without stranding its environment -------------------------------------------


@dataclass(frozen=True)
class DeleteBlocked:
    """Why runs cannot be deleted yet. `forceable` says whether the caller may override it."""

    reason: DeleteRefusalReason
    run_ids: tuple[str, ...]
    detail: str
    forceable: bool

    def body(self) -> dict[str, object]:
        """The 409 body (contracts.run.DeleteRefusal). `detail` is the sentence every surface
        shows; the rest lets a client offer the one action that unblocks the delete (the GUI's
        "Delete anyway")."""
        return DeleteRefusal(
            detail=self.detail,
            reason=self.reason,
            run_ids=self.run_ids,
            forceable=self.forceable,
        ).model_dump(mode="json")


def check_deletable(run_ids: Sequence[str], *, force: bool = False) -> DeleteBlocked | None:
    """Make these runs safe to delete, or say why they are not.

    The run row is the release sweeper's only handle on a run (#173): deleting it while the
    environment is still up leaves the container until the next boot and the tailnet nodes
    forever. So, in order:
      running     a run that has not finished still has its environment up. Never forceable:
                  terminate it first, which releases it.
      grading     its grade is in flight, and grading may still need the environment. Never
                  forceable: it settles on its own within moments.
      unreleased  a finished run whose environment is still up is released here, synchronously.
                  If that fails (Docker or Headscale down), the delete is refused unless `force`,
                  which accepts the leak and is logged as such.
    """
    from xorcise.core.rest.run_terminate import is_grading

    unfinished = [
        (r, entry.state)
        for r in run_ids
        if (entry := runs.get(r)) is not None and not runs.terminal_state(r)[0]
    ]
    if unfinished:
        # Name each run's state rather than calling it "running": the gate is `state !=
        # "terminal"`, which also catches a `created` run that never started, where "still
        # running" describes nothing that is happening.
        named = ", ".join(f"{r} (state: {state})" for r, state in unfinished)
        plural = len(unfinished) > 1
        return DeleteBlocked(
            "running",
            tuple(r for r, _ in unfinished),
            f"{'runs' if plural else 'run'} {named} {'have' if plural else 'has'} not finished. "
            f"Terminate {'them' if plural else 'it'} first: deleting a run that has not finished "
            "would leave its environment up with nothing left to clean it up.",
            forceable=False,
        )
    grading = [r for r in run_ids if is_grading(r)]
    if grading:
        return DeleteBlocked(
            "grading",
            tuple(grading),
            f"{_runs_phrase(grading)} still being graded. Delete once the result is recorded, "
            "usually within a minute.",
            forceable=False,
        )
    failed: list[tuple[str, str]] = []
    for run_id in run_ids:
        state = runs.release_state(run_id)
        if state is None or state.released_at is not None or not runs.has_environment(run_id):
            continue
        outcome = teardown_run(run_id)
        if not outcome.released:
            failed.append((run_id, outcome.error))
    if not failed:
        return None
    if force:
        for run_id, error in failed:
            log.warning(
                "deleting %s with its environment possibly still up (forced; teardown: %s)",
                run_id,
                error,
            )
        return None
    ids = [r for r, _ in failed]
    errors = "; ".join(sorted({e for _, e in failed}))
    return DeleteBlocked(
        "unreleased",
        tuple(ids),
        f"the environment of {_runs_phrase(ids, verb='')} could not be torn down ({errors}). "
        "Deleting now would leave its container or tailnet nodes running with nothing left to "
        "clean them up. Once Docker and Headscale are back (xorcise up), the release is retried "
        "automatically and the delete will go through; or delete anyway to accept the leak.",
        forceable=True,
    )


def _runs_phrase(run_ids: Sequence[str], *, verb: str = "is") -> str:
    """'run abc is' / 'runs abc, def are' — names every run, so the refusal says which."""
    plural = len(run_ids) > 1
    head = f"runs {', '.join(run_ids)}" if plural else f"run {run_ids[0]}"
    if not verb:
        return head
    return f"{head} {'are' if plural else verb}"
