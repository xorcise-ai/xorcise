"""Reconcile live orchestration state to the persisted runs on boot (rest layer).

Durable state is authoritative; the in-memory maps are caches. A server restart (or a
second worker starting) must converge the live Headscale/Docker world to the persisted non-terminal
runs. That convergence stands on one invariant: teardown + status are keyed on
the deterministic run-id container name, so this process can act on containers it never tracked.

What it does, per non-terminal run:
  * re-assert the DB-authoritative ACL once (a crash mid-create could have left a rule un-rendered);
  * a run still only RESERVED (deploy never finalized — crash between reserve and finalize) is
    released: tear down whatever environment exists and delete the reservation (it never ran, so
    nothing is graded and its subnet is freed);
  * a DEPLOYED run whose container is still live is adopted (left running — the watchdog owns its
    deadline); one whose container is gone is aborted WITHOUT grading (a server crash is our infra
    failure, so we do not penalise the agent with a phantom score) — mark terminal + release.

The budget/terminal timers are NOT rebuilt here: the watchdog reads deadlines from the DB every
tick, so they self-heal. Pure + injectable: unit tests drive it with stub ports; boot wires it with
the real deps (build_run_create_deps).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from xorcise.core import runs
from xorcise.core.config import get_settings
from xorcise.core.contracts.control import RunState
from xorcise.core.contracts.errors import NotFoundError

if TYPE_CHECKING:
    from xorcise.core.contracts.control import StatusResult, TeardownResult

log = logging.getLogger(__name__)


class _ControlLike(Protocol):
    """The slice of ControlPort reconcile uses (kept narrow so test fakes satisfy it too)."""

    def status(self, run_id: str, *, credential: str) -> StatusResult: ...
    def teardown(self, run_id: str, *, credential: str) -> TeardownResult: ...
    def reap_orphan_environments(self, keep: Sequence[str], *, credential: str) -> list[str]: ...


class _FenceLike(Protocol):
    """The slice of NetworkFencePort reconcile uses."""

    def reconcile_acl(self) -> None: ...
    def teardown_run_network(self, run_id: str) -> None: ...


class _ReconcileDeps(Protocol):
    """The deps reconcile needs — satisfied structurally by RunCreateDeps and by test stubs.

    Read-only members (covariant) so a deps whose control/fence are concrete subtypes still
    matches — a read-write protocol attribute would be invariant and reject them."""

    @property
    def control(self) -> _ControlLike: ...
    @property
    def fence(self) -> _FenceLike: ...
    @property
    def api_key(self) -> str: ...


@dataclass
class ReconcileReport:
    """What the boot reconcile did, for logging + tests."""

    adopted: list[str] = field(default_factory=list)  # container still live — left running
    aborted: list[str] = field(default_factory=list)  # deployed but container gone — closed out
    released: list[str] = field(default_factory=list)  # reserved only, deploy never finished


def _teardown_env(deps: _ReconcileDeps, run_id: str) -> None:
    """Best-effort release of a run's environment (container + tailnet nodes). Idempotent.

    Uses the injected deps (not rest.run_teardown, which builds its own) so reconcile stays
    injectable. Both underlying teardowns are keyed on the run-id name, so they
    act on containers/nodes this process never tracked. Failures are logged, never raised."""
    try:
        deps.control.teardown(run_id, credential=deps.api_key)
    except Exception:
        log.warning("reconcile: control.teardown failed for %s", run_id, exc_info=True)
    try:
        deps.fence.teardown_run_network(run_id)
    except Exception:
        log.warning("reconcile: fence.teardown_run_network failed for %s", run_id, exc_info=True)


def _reconcile_one(
    deps: _ReconcileDeps,
    run_id: str,
    was_deployed: bool,
    now_fn: Callable[[], datetime],
    report: ReconcileReport,
) -> None:
    """Converge one non-terminal run. Raises on an unexpected error so the caller can isolate it."""
    if not was_deployed:
        # Reserved (subnet allocated, tailnet node maybe minted, container maybe created) but deploy
        # never finalized — the server crashed between reserve and finalize. Release whatever
        # exists then drop the reservation; nothing was graded (it never ran). Teardown BEFORE
        # delete so a crash in between leaves the reservation (revisited next boot), never a
        # leaked environment.
        _teardown_env(deps, run_id)
        runs.delete_run(run_id)
        report.released.append(run_id)
        return
    try:
        # PENDING counts as LIVE: the environment is deployed and still coming up (inner services
        # starting). Keying this on READY alone would abort a healthy run whose only crime was to be
        # mid-startup when the server restarted. FAILED is NOT live — the environment died at
        # deploy, so the run is unrecoverable and falls through to the abort below.
        live = deps.control.status(run_id, credential=deps.api_key).state in {
            RunState.READY,
            RunState.PENDING,
        }
    except NotFoundError:
        live = False  # the runner has no container under this run-id name — it is gone
    if live:
        report.adopted.append(run_id)  # still running — the watchdog owns its deadline
        return
    # Deployed but the container is gone (crash / reaped) — unrecoverable. Abort WITHOUT grading:
    # a server crash is our infra failure, so we do not record a phantom score against the agent.
    # Teardown BEFORE mark_terminal (crash-safety): a crash in between leaves the
    # run NON-terminal so the next boot revisits + retries it, rather than terminal-but-leaked
    # (reconcile only scans non-terminal runs, so a terminal run is never revisited).
    _teardown_env(deps, run_id)
    runs.mark_terminal(run_id, "crashed", now_fn())
    report.aborted.append(run_id)


def reconcile_on_startup(
    deps: _ReconcileDeps | None = None,
    *,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> ReconcileReport:
    """Converge live Headscale/Docker to the persisted non-terminal runs."""
    if deps is None:
        from xorcise.core.rest.run_create import build_run_create_deps

        deps = build_run_create_deps(get_settings())
    report = ReconcileReport()
    # Re-assert the DB-authoritative ACL: a crash mid-create could have left a run's rule
    # un-rendered; this restores the complete policy from every non-terminal run's entry_cidrs.
    deps.fence.reconcile_acl()
    active = runs.active_runs_to_reconcile()
    # Sweep environments whose network outlived their run: a leaked network keeps its subnet
    # allocated, so the run pool would bleed a /24 per imperfect teardown. Every NON-TERMINAL run is
    # kept — reaping a live run's network would cut its agent off mid-run. Best-effort: this is
    # cleanup, so a Docker hiccup must not stop the run convergence below.
    try:
        reaped = deps.control.reap_orphan_environments(
            [run_id for run_id, _ in active], credential=deps.api_key
        )
        if reaped:
            log.info("reconcile: reaped %d orphaned environment(s): %s", len(reaped), reaped)
    except Exception:
        log.warning("reconcile: orphan environment sweep failed", exc_info=True)
    for run_id, was_deployed in active:
        # Isolate each run: a transient Docker/Headscale error on one run must not
        # abort the whole loop. A failed run is left NON-terminal, so the next boot retries it.
        try:
            _reconcile_one(deps, run_id, was_deployed, now_fn, report)
        except Exception:
            log.warning(
                "reconcile: failed to reconcile %s (left for the next boot)", run_id, exc_info=True
            )
    log.info(
        "reconcile: adopted=%d aborted=%d released=%d",
        len(report.adopted),
        len(report.aborted),
        len(report.released),
    )
    return report


def reconcile_all_on_startup() -> None:
    """Run every boot reconcile: the run/tailnet/docker convergence, the ingest
    and pull jobs, and the orphaned-grade sweep. The single entrypoint the role_all
    startup task schedules on a worker thread, so the boot wiring stays a trivial, obviously-correct
    call."""
    from xorcise.core.rest.ingest_jobs import reconcile_ingest_jobs
    from xorcise.core.rest.pull_jobs import reconcile_pull_jobs
    from xorcise.core.rest.run_terminate import regrade_orphaned_terminal_runs

    reconcile_on_startup()
    reconcile_ingest_jobs()
    reconcile_pull_jobs()
    # Heal any run wedged at "grading" because its background grade was lost to a stop/crash — the
    # convergence above only touches NON-terminal runs, so a terminal-ungraded run needs this.
    regrade_orphaned_terminal_runs()
