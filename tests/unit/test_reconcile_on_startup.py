"""Boot reconcile converges live Headscale/Docker to the persisted runs.

Drives reconcile_on_startup with hand-built stub ports so each branch is asserted precisely:
re-assert the ACL, adopt a live container, abort a deployed run whose container is gone (WITHOUT
grading — a server crash is our infra failure), and release a reserved-only run that never deployed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from xorcise.core import runs
from xorcise.core.contracts.control import RunState, StatusResult, TeardownResult
from xorcise.core.contracts.errors import NotFoundError
from xorcise.core.rest.reconcile import reconcile_on_startup


class _FakeControl:
    def __init__(self, live: set[str]) -> None:
        self._live = set(live)
        self.torn_down: list[str] = []

    def status(self, run_id: str, *, credential: str) -> StatusResult:
        if run_id in self._live:
            return StatusResult(run_id=run_id, state=RunState.READY, ready=True)
        raise NotFoundError(run_id)

    def teardown(self, run_id: str, *, credential: str) -> TeardownResult:
        self.torn_down.append(run_id)
        self._live.discard(run_id)
        return TeardownResult(run_id=run_id, ok=True)

    def reap_orphan_environments(self, keep: Sequence[str], *, credential: str) -> list[str]:
        return []


class _FakeFence:
    def __init__(self) -> None:
        self.reconciled = 0
        self.networks_torn: list[str] = []
        self.fail = False  # the teardown raises, as a Headscale call does when the plane is down

    def reconcile_acl(self) -> None:
        self.reconciled += 1

    def teardown_run_network(self, run_id: str) -> None:
        if self.fail:
            raise RuntimeError("Headscale control plane 'headscale' is not reachable")
        self.networks_torn.append(run_id)


@dataclass
class _Deps:
    control: _FakeControl
    fence: _FakeFence
    api_key: str = "local"


def _now() -> datetime:
    return datetime(2026, 7, 2, tzinfo=UTC)


def _deployed(run_id: str, cidr: str) -> None:
    """Persist a run as if it finished deploy (reserve + finalize → prompt set)."""
    runs.reserve_run(run_id, "ag", "c", network_cidr=cidr, entry_cidrs=cidr)
    runs.finalize_run(run_id, budget_seconds=300, prompt="P")


def test_reaps_orphan_environments_keeping_every_non_terminal_run(migrated_home):
    # A leaked network keeps its subnet allocated; boot is where we sweep them. The keep-set must be
    # every NON-TERMINAL run — reaping a live run's network would cut its agent off mid-run.
    _deployed("live", "10.200.1.0/24")
    runs.reserve_run(
        "reserved", "ag", "c", network_cidr="10.200.2.0/24", entry_cidrs="10.200.2.0/24"
    )

    class _Reaping(_FakeControl):
        def __init__(self, live: set[str]) -> None:
            super().__init__(live)
            self.kept: list[str] = []

        def reap_orphan_environments(self, keep, *, credential: str) -> list[str]:
            self.kept = sorted(keep)
            return ["orphan-project"]

    control = _Reaping({"live"})
    reconcile_on_startup(_Deps(control, _FakeFence()), now_fn=_now)
    assert control.kept == ["live", "reserved"]  # both non-terminal runs are protected


def test_a_failing_reap_never_breaks_boot_reconcile(migrated_home):
    # Best-effort: the sweep is a cleanup, so a Docker hiccup must not stop the run convergence.
    class _BoomReap(_FakeControl):
        def reap_orphan_environments(self, keep, *, credential: str) -> list[str]:
            raise RuntimeError("docker down")

    _deployed("live", "10.200.1.0/24")
    report = reconcile_on_startup(_Deps(_BoomReap({"live"}), _FakeFence()), now_fn=_now)
    assert report.adopted == ["live"]


def test_reasserts_the_acl_even_with_no_runs(migrated_home):
    fence = _FakeFence()
    report = reconcile_on_startup(_Deps(_FakeControl(set()), fence), now_fn=_now)
    assert fence.reconciled == 1
    assert report.adopted == [] and report.aborted == [] and report.released == []


def test_adopts_a_run_whose_container_is_still_live(migrated_home):
    _deployed("live", "10.200.1.0/24")
    control = _FakeControl({"live"})
    fence = _FakeFence()
    report = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert report.adopted == ["live"]
    assert control.torn_down == [] and fence.networks_torn == []
    assert runs.terminal_state("live")[0] is False  # left running


def test_adopts_a_run_whose_environment_is_still_starting(migrated_home):
    # A server restart DURING startup must not abort a healthy run: PENDING (inner services still
    # coming up) is alive, not gone. Keying "live" on READY alone would tear down a run that was
    # seconds from ready.
    _deployed("booting", "10.200.1.0/24")

    class _Starting(_FakeControl):
        def status(self, run_id: str, *, credential: str) -> StatusResult:
            return StatusResult(run_id=run_id, state=RunState.PENDING, ready=False)

    control = _Starting(set())
    fence = _FakeFence()
    report = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert report.adopted == ["booting"]
    assert control.torn_down == [] and fence.networks_torn == []
    assert runs.terminal_state("booting")[0] is False


def test_aborts_a_deployed_run_whose_environment_failed(migrated_home):
    # FAILED (the environment died at deploy) is unrecoverable — closed out like a gone container,
    # and still WITHOUT grading, since a broken environment is our infra failure not the agent's.
    _deployed("dead", "10.200.1.0/24")

    class _Failed(_FakeControl):
        def status(self, run_id: str, *, credential: str) -> StatusResult:
            return StatusResult(run_id=run_id, state=RunState.FAILED, ready=False)

    control = _Failed(set())
    fence = _FakeFence()
    report = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert report.aborted == ["dead"]
    assert runs.terminal_state("dead")[0] is True
    assert "dead" in control.torn_down and "dead" in fence.networks_torn


def test_aborts_a_deployed_run_whose_container_is_gone_without_grading(migrated_home):
    _deployed("lost", "10.200.1.0/24")
    control = _FakeControl(set())  # container absent → status NotFound
    fence = _FakeFence()
    report = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert report.aborted == ["lost"]
    is_term, trigger, _ = runs.terminal_state("lost")
    assert is_term and trigger == "crashed"
    assert "lost" in control.torn_down and "lost" in fence.networks_torn
    # abort does NOT grade — no result recorded for our infra failure
    from xorcise.core import reporting

    assert reporting.get_result("lost") is None


def test_releases_a_reserved_only_run_that_never_finished_deploy(migrated_home):
    runs.reserve_run("res", "ag", "c", network_cidr="10.200.9.0/24", entry_cidrs="10.200.9.0/24")
    control = _FakeControl(set())
    fence = _FakeFence()
    report = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert report.released == ["res"]
    assert runs.get("res") is None  # reservation deleted → subnet freed
    assert runs.active_cidrs() == set()
    assert "res" in control.torn_down and "res" in fence.networks_torn


def test_reconcile_is_idempotent_on_replay(migrated_home):
    # reconcile keys off DB state it mutates (delete/terminal), so a second run —
    # a retried boot, or two workers starting — is a clean no-op, never a double-teardown.
    _deployed("lost", "10.200.1.0/24")
    runs.reserve_run("res", "ag", "c", network_cidr="10.200.9.0/24", entry_cidrs="10.200.9.0/24")
    control = _FakeControl(set())
    fence = _FakeFence()

    first = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert set(first.aborted) == {"lost"} and set(first.released) == {"res"}

    second = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert second.aborted == [] and second.released == [] and second.adopted == []
    assert control.torn_down == ["lost", "res"]  # no re-teardown on the replay


def test_aborted_run_is_torn_down_before_marked_terminal(migrated_home):
    # crash-safety: teardown happens BEFORE mark_terminal, so a crash between them
    # leaves the run NON-terminal (revisited next boot) rather than terminal-but-leaked. Assert the
    # environment is released whenever the run is finally terminal.
    _deployed("lost", "10.200.1.0/24")
    control = _FakeControl(set())
    fence = _FakeFence()
    reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert "lost" in control.torn_down and "lost" in fence.networks_torn
    assert runs.terminal_state("lost")[0] is True


def test_one_failing_run_does_not_block_the_others(migrated_home):
    # a per-run failure (transient Docker/Headscale error) is isolated — the run
    # stays non-terminal (retried next boot) and the OTHER runs still reconcile.
    _deployed("boom", "10.200.1.0/24")
    _deployed("ok", "10.200.2.0/24")

    class _AngryControl(_FakeControl):
        def status(self, run_id: str, *, credential: str):
            if run_id == "boom":
                raise RuntimeError("headscale flaked")
            return super().status(run_id, credential=credential)

    control = _AngryControl(set())  # neither is "live" → both would abort
    fence = _FakeFence()
    report = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert report.aborted == ["ok"]  # the healthy run was closed out
    assert runs.terminal_state("ok")[0] is True
    assert runs.terminal_state("boom")[0] is False  # failed run left for the next boot


def test_reconcile_all_on_startup_runs_runs_then_ingest(migrated_home, monkeypatch):
    # the single boot entrypoint the role_all startup task schedules
    # runs BOTH reconciles (runs/tailnet/docker convergence, then ingest jobs), in that order.
    import xorcise.core.rest.ingest_jobs as ingest_jobs
    import xorcise.core.rest.reconcile as reconcile_mod

    calls: list[str] = []

    def converge() -> reconcile_mod.ReconcileReport:
        calls.append("runs")
        return reconcile_mod.ReconcileReport(adopted=["a"], aborted=["b", "c"])

    monkeypatch.setattr(reconcile_mod, "reconcile_on_startup", converge)
    monkeypatch.setattr(ingest_jobs, "reconcile_ingest_jobs", lambda: calls.append("ingest"))
    reconcile_mod.reconcile_all_on_startup(now_fn=_now)
    assert calls == ["runs", "ingest"]
    status = reconcile_mod.boot_reconcile_status()
    assert (status.state, status.adopted, status.aborted, status.finished_at) == (
        "done",
        1,
        2,
        _now(),
    )


def test_a_failed_step_does_not_skip_the_others_and_is_published(migrated_home, monkeypatch):
    """#173: Headscale down at boot makes the run convergence raise. That used to skip every later
    step (a lost grade was never re-driven) and say nothing outside serve.log."""
    import xorcise.core.rest.ingest_jobs as ingest_jobs
    import xorcise.core.rest.reconcile as reconcile_mod
    import xorcise.core.rest.run_terminate as run_terminate

    calls: list[str] = []

    def converge() -> reconcile_mod.ReconcileReport:
        raise RuntimeError("Headscale control plane 'headscale' is not reachable\nmore detail")

    monkeypatch.setattr(reconcile_mod, "reconcile_on_startup", converge)
    monkeypatch.setattr(ingest_jobs, "reconcile_ingest_jobs", lambda: calls.append("ingest"))
    monkeypatch.setattr(
        run_terminate, "regrade_orphaned_terminal_runs", lambda: calls.append("regrade")
    )
    with pytest.raises(RuntimeError, match="not reachable"):
        reconcile_mod.reconcile_all_on_startup(now_fn=_now)
    assert calls == ["ingest", "regrade"]  # the later steps still ran
    status = reconcile_mod.boot_reconcile_status()
    assert status.state == "failed"
    assert status.error == "runs: Headscale control plane 'headscale' is not reachable"


def test_a_reservation_whose_teardown_fails_is_closed_for_the_sweeper(migrated_home):
    """#173: the reservation is the only record that the environment exists, so it is never
    dropped after a failed teardown (that leaked its tailnet user and router node for good). Nor is
    it kept as a reservation: that read as a live run until the next boot, blocking `agent rm`, and
    the suggested "terminate it" recorded a graded 0.0 (review of #185). It is closed as an
    ungraded deploy_failed, and the release sweeper retries the teardown."""
    from xorcise.core.contracts.run import UNGRADED_TRIGGERS

    runs.reserve_run("res", "a1", "c", network_cidr="10.200.7.0/24", entry_cidrs="")
    fence = _FakeFence()
    fence.fail = True
    report = reconcile_on_startup(_Deps(_FakeControl(set()), fence), now_fn=_now)
    assert report.release_pending == ["res"]
    assert report.released == []
    is_term, trigger, _ = runs.terminal_state("res")
    assert is_term and trigger == "deploy_failed" and trigger in UNGRADED_TRIGGERS
    entry = runs.get("res")
    assert entry is not None and "deploy finished" in (entry.terminal_detail or "")
    state = runs.release_state("res")
    assert state is not None and state.released_at is None and state.attempts == 1
    assert [a.run_id for a in runs.terminal_runs_awaiting_release()] == ["res"]


def test_a_run_that_fails_to_converge_is_reported(migrated_home, monkeypatch):
    """Review of #185: the status could read `done` with zero counts while runs were skipped."""
    import xorcise.core.rest.reconcile as reconcile_mod

    _deployed("boom", "10.200.9.0/24")

    def explode(*a, **k):
        raise RuntimeError("docker inspect failed")

    monkeypatch.setattr(reconcile_mod, "_reconcile_one", explode)
    report = reconcile_on_startup(_Deps(_FakeControl(set()), _FakeFence()), now_fn=_now)
    assert report.failed == ["boom"]


def test_an_aborted_run_whose_teardown_fails_is_left_to_the_sweeper(migrated_home):
    _deployed("gone", "10.200.8.0/24")
    fence = _FakeFence()
    fence.fail = True
    report = reconcile_on_startup(_Deps(_FakeControl(set()), fence), now_fn=_now)
    assert report.aborted == ["gone"]
    state = runs.release_state("gone")
    assert state is not None
    assert state.released_at is None
    assert state.attempts == 1
    assert state.error is not None and state.error.startswith("tailnet: ")


def test_terminal_runs_are_left_untouched(migrated_home):
    _deployed("done", "10.200.1.0/24")
    runs.mark_terminal("done", "done", _now())
    control = _FakeControl(set())
    fence = _FakeFence()
    report = reconcile_on_startup(_Deps(control, fence), now_fn=_now)
    assert report.adopted == [] and report.aborted == [] and report.released == []
    assert control.torn_down == []
