"""Background readiness gate (rest layer).

`deploy()` only STARTS the outer lifecycle container — the mission stack and the per-run subnet
router come up asynchronously INSIDE it, and the runner deliberately does not block on that ("the
live wait-ready is an integration concern"). Nothing then checked the outcome, so an environment
that died at deploy, or never finished coming up, left the run non-terminal FOREVER with a live
agent working against a target that did not exist.

This closes that hole with a periodic, DB-driven scan (mirroring BudgetWatchdog): for each deployed
non-terminal run inside its readiness window, ask BOTH planes —

  * runner (ControlPort.status): the outer container is alive AND every inner compose service runs;
  * fence (NetworkFencePort.router_online): the run's subnet router actually joined the tailnet,

because "services up" alone still permits the observed failure where the agent gets a tailnet IP and
no route to any target. A run whose environment is FAILED is closed out at once; one that is merely
still starting is left alone until its window expires. Either way the run is terminated as
`deploy_failed` and its environment released, rather than sitting in limbo.

Stateless per tick and driven off persisted state, so it self-heals across a server restart: no
in-memory bookkeeping to lose, and a replay of the same tick is a no-op (terminate is idempotent).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Protocol

from xorcise.core.contracts.control import RunState
from xorcise.core.contracts.errors import NotFoundError

if TYPE_CHECKING:
    from xorcise.core.contracts.control import StatusResult, TeardownResult

log = logging.getLogger(__name__)

#: The trigger recorded for a run whose environment never came up. Distinct from "timeout" (the
#: agent ran out of BUDGET, which is a legitimate result) — this one is an environment failure.
DEPLOY_FAILED = "deploy_failed"


class _ControlLike(Protocol):
    """The slice of ControlPort the gate uses (kept narrow so test fakes satisfy it too)."""

    def status(self, run_id: str, *, credential: str) -> StatusResult: ...
    def teardown(self, run_id: str, *, credential: str) -> TeardownResult: ...


class _FenceLike(Protocol):
    def router_online(self, run_id: str) -> bool: ...
    def teardown_run_network(self, run_id: str) -> None: ...


class _ReadinessDeps(Protocol):
    """Satisfied structurally by RunCreateDeps and by test stubs (read-only ⇒ covariant)."""

    @property
    def control(self) -> _ControlLike: ...
    @property
    def fence(self) -> _FenceLike: ...
    @property
    def api_key(self) -> str: ...


#: The gate's most recent observation per run: run_id -> (state, detail). A CACHE, not a source of
#: truth (like RunnerControlService._deployed) — it lets the run page report the environment's live
#: state without re-running the gate's Docker/Headscale probes on every poll. Absent ⇒ not observed
#: yet, which the reader degrades to "starting" rather than inventing a verdict.
_OBSERVED: dict[str, tuple[str, str]] = {}


def observed_environment(run_id: str) -> tuple[str, str] | None:
    """The gate's last (state, detail) for this run, or None if it has not been scanned yet."""
    return _OBSERVED.get(run_id)


def classify_environment(deps: _ReadinessDeps, run_id: str) -> tuple[str, str]:
    """(state, detail) for a run that HAS an environment, probing both planes.

    Never raises: a missing container reads as still-starting, since the gate's window (not a single
    probe) decides when absence becomes a failure."""
    try:
        state = deps.control.status(run_id, credential=deps.api_key).state
    except NotFoundError:
        return "starting", "waiting for the mission environment to start"
    if state == RunState.FAILED:
        return "failed", "the mission environment exited"
    if state != RunState.READY:
        return "starting", "mission services are still coming up"
    if not deps.fence.router_online(run_id):
        # Services up but no route: the agent would join the tailnet and reach nothing.
        return "starting", "waiting for the run's subnet router to join the tailnet"
    return "ready", ""


def environment_ready(deps: _ReadinessDeps, run_id: str) -> bool:
    """True iff BOTH planes report this run's environment usable.

    Runner: outer container alive + every inner service running (RunState.READY). Fence: the run's
    subnet router is online, so the advertised route to the targets actually exists. A fence that
    cannot report degrades to True (its port default), so this never blocks on an unreportable
    plane — the runner verdict alone still gates."""
    if deps.control.status(run_id, credential=deps.api_key).state != RunState.READY:
        return False
    return deps.fence.router_online(run_id)


class ReadinessWatchdog:
    """Periodic scan closing out runs whose environment failed or never became ready."""

    def __init__(
        self,
        deps: _ReadinessDeps,
        list_pending: Callable[[], list[tuple[str, datetime]]],
        terminate: Callable[[str, str, datetime], object],
        now_fn: Callable[[], datetime],
        timeout_seconds: float,
        interval: float = 5.0,
        strikes: int = 2,
    ) -> None:
        self._deps = deps
        self._list_pending = list_pending
        self._terminate = terminate
        self._now = now_fn
        self._timeout = timeout_seconds
        self._interval = interval
        self._strikes = strikes
        self._task: asyncio.Task[None] | None = None
        # Runs observed ready at least once. The startup window applies ONLY before that: a probe
        # can blip (node_online_by_name degrades to False on ANY Headscale error), and a run that
        # already came up must never be killed by one bad sample hours later.
        self._ever_ready: set[str] = set()
        # Consecutive not-ready observations per run, so expiry needs sustained failure, not one
        # unlucky poll. Reset the moment a run reads ready.
        self._misses: dict[str, int] = {}

    def tick(self) -> int:
        """One scan. Returns how many runs were closed out."""
        now = self._now()
        pending = self._list_pending()
        # Drop bookkeeping for runs that left the scan set (terminated / closed out) so neither map
        # grows without bound over a long-lived server process.
        live = {run_id for run_id, _ in pending}
        self._ever_ready &= live
        self._misses = {k: v for k, v in self._misses.items() if k in live}
        for gone in [k for k in _OBSERVED if k not in live]:
            del _OBSERVED[gone]  # terminal runs report "released" from the run row, not this cache
        fired = 0
        for run_id, created_at in pending:
            # Isolate each run: a transient Docker/Headscale error on one must not abort the scan
            # (the next tick retries it), exactly as the boot reconcile does.
            try:
                fired += int(self._check_one(run_id, created_at, now))
            except Exception:
                log.warning(
                    "readiness: check failed for %s (retried next tick)", run_id, exc_info=True
                )
        return fired

    def _check_one(self, run_id: str, created_at: datetime, now: datetime) -> bool:
        # ONE classification drives both the gate's verdict and what the run page reports, so the
        # UI can never disagree with the decision that terminates (or spares) the run.
        state, detail = classify_environment(self._deps, run_id)
        _OBSERVED[run_id] = (state, detail)
        ready = state == "ready"
        failed = state == "failed"
        if ready:
            self._ever_ready.add(run_id)
            self._misses.pop(run_id, None)
            return False
        if not failed:
            # The startup window is a STARTUP gate: once a run has come up, a later not-ready
            # reading is a probe blip or a mid-run wobble, never evidence the deploy failed. Only
            # an outright FAILED environment (above) closes such a run out.
            if run_id in self._ever_ready:
                return False
            if now < created_at + timedelta(seconds=self._timeout):
                return False  # still coming up, inside its window — leave it alone
            misses = self._misses.get(run_id, 0) + 1
            self._misses[run_id] = misses
            if misses < self._strikes:
                return False  # sustained failure only — one bad sample is not a verdict
        reason = "environment failed" if failed else "not ready within the readiness window"
        log.warning("readiness: closing out %s — %s", run_id, reason)
        self._release(run_id)
        self._terminate(run_id, DEPLOY_FAILED, now)
        return True

    def _release(self, run_id: str) -> None:
        """Best-effort teardown of the run's environment. Failures are logged, never raised —
        the run must still be marked terminal so it cannot sit in limbo."""
        try:
            self._deps.control.teardown(run_id, credential=self._deps.api_key)
        except Exception:
            log.warning("readiness: control.teardown failed for %s", run_id, exc_info=True)
        try:
            self._deps.fence.teardown_run_network(run_id)
        except Exception:
            log.warning("readiness: fence teardown failed for %s", run_id, exc_info=True)

    async def run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            # Worker thread: a tick does Docker/Headscale I/O (container inspect, a nested
            # `compose ps` exec, a headscale query), which must never block the event loop.
            await asyncio.to_thread(self.tick)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
