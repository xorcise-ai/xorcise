"""Readiness gate: close out a run whose environment never came up.

`deploy()` returns before the mission stack exists, so a run could sit non-terminal forever with a
live agent working against a target that never came up (the reported incident: the environment died
at deploy, the run stayed 'created', the agent joined the tailnet and every target connect failed).

The gate is a periodic, DB-driven tick (mirroring BudgetWatchdog): per deployed non-terminal run in
its readiness window it asks BOTH planes — the runner (outer container + inner services) and the
fence (the run's subnet router is online) — and terminates the run as `deploy_failed` when the
environment has died or never became ready in time. Stateless per tick, so it self-heals across a
server restart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from xorcise.core.contracts.control import RunState, StatusResult, TeardownResult
from xorcise.core.contracts.errors import NotFoundError
from xorcise.core.rest.run_readiness import (
    ReadinessWatchdog,
    closeout_detail,
    observed_environment,
)

T0 = datetime(2026, 7, 28, 12, 0, 0, tzinfo=UTC)


class _Control:
    def __init__(
        self,
        state: RunState = RunState.READY,
        missing: bool = False,
        *,
        detail: str = "",
        logs: str = "",
    ) -> None:
        self.state = state
        self.missing = missing
        self.detail = detail
        self.logs = logs
        self.torn_down: list[str] = []
        self.log_reads: list[str] = []

    def status(self, run_id: str, *, credential: str) -> StatusResult:
        if self.missing:
            raise NotFoundError(run_id)
        return StatusResult(
            run_id=run_id,
            state=self.state,
            ready=self.state == RunState.READY,
            detail=self.detail,
        )

    def teardown(self, run_id: str, *, credential: str) -> TeardownResult:
        self.torn_down.append(run_id)
        return TeardownResult(run_id=run_id, ok=True)

    def environment_logs(self, run_id: str, *, credential: str) -> str:
        self.log_reads.append(run_id)
        return self.logs


class _Fence:
    def __init__(self, router_up: bool = True) -> None:
        self.router_up = router_up
        self.torn_down: list[str] = []

    def router_online(self, run_id: str) -> bool:
        return self.router_up

    def teardown_run_network(self, run_id: str) -> None:
        self.torn_down.append(run_id)

    def reconcile_acl(self) -> None: ...


class _Deps:
    def __init__(self, control: _Control, fence: _Fence) -> None:
        self.control = control
        self.fence = fence
        self.api_key = "k"


class _Recorder:
    """What the gate handed the terminator: (run_id, trigger) per call, the detail per run, and —
    for the ordering test — how many log reads had happened by the time terminate was called."""

    def __init__(self, control: _Control) -> None:
        self._control = control
        self.calls: list[tuple[str, str]] = []
        self.details: dict[str, str] = {}
        self.log_reads_at_terminate: list[int] = []

    def terminate(self, run_id: str, trigger: str, at: datetime, detail: str) -> None:
        self.calls.append((run_id, trigger))
        self.details[run_id] = detail
        self.log_reads_at_terminate.append(len(self._control.log_reads))


def _gate(
    control: _Control,
    fence: _Fence,
    runs: list[tuple[str, datetime]],
    *,
    now: datetime = T0,
    timeout: float = 90.0,
) -> tuple[ReadinessWatchdog, _Recorder]:
    rec = _Recorder(control)
    wd = ReadinessWatchdog(
        deps=_Deps(control, fence),
        list_pending=lambda: runs,
        terminate=rec.terminate,
        now_fn=lambda: now,
        timeout_seconds=timeout,
    )
    return wd, rec


def _watchdog(
    control: _Control,
    fence: _Fence,
    runs: list[tuple[str, datetime]],
    *,
    now: datetime = T0,
    timeout: float = 90.0,
) -> tuple[ReadinessWatchdog, list[tuple[str, str]]]:
    wd, rec = _gate(control, fence, runs, now=now, timeout=timeout)
    return wd, rec.calls


def test_a_failed_environment_is_terminated_immediately_not_after_the_timeout():
    # The environment DIED (outer container exited) — waiting out the window would only delay the
    # inevitable while the agent keeps working a dead target.
    control, fence = _Control(RunState.FAILED), _Fence()
    wd, terminated = _watchdog(control, fence, [("r1", T0)])
    assert wd.tick() == 1
    assert terminated == [("r1", "deploy_failed")]


def test_the_gate_releases_the_environment_through_terminate_only():
    """#43 finding 4. The gate used to tear down itself AND hand the run to terminate_run, whose
    grade_and_record tears down again in a `finally` — every close-out released the same
    environment twice (the "removal already in progress" 409s), doubling its latency. Release is
    the terminator's job; the gate's is to decide, capture, and hand over."""
    control, fence = _Control(RunState.FAILED), _Fence()
    wd, rec = _gate(control, fence, [("r1", T0)])
    assert wd.tick() == 1
    assert rec.calls == [("r1", "deploy_failed")]
    assert control.torn_down == [] and fence.torn_down == []


def test_close_out_records_why_and_the_evidence_read_before_release():
    """#43 finding 2. Closing out force-removed the outer container, so the logs that said WHY
    `compose up` failed were gone before anyone could read them. The gate now reads them first
    and records them, with its verdict, as the run's terminal detail."""
    logs = "xorcise: inner dockerd did not come up within 120s\nfailed to start daemon: no space"
    control, fence = _Control(RunState.FAILED, logs=logs), _Fence()
    wd, rec = _gate(control, fence, [("r1", T0)])
    assert wd.tick() == 1
    detail = rec.details["r1"]
    assert detail.startswith("the environment failed — the mission environment exited")
    assert "inner dockerd did not come up" in detail and "no space" in detail
    # The read happened BEFORE the hand-over that releases the environment.
    assert control.log_reads == ["r1"]
    assert rec.log_reads_at_terminate == [1]


def test_the_runners_own_detail_is_what_the_gate_reports_and_records():
    """#43 finding 3, gate side. The runner now says WHY it is not ready (an inner daemon that is
    not answering); the run page shows that, and a close-out records it."""
    why = "the mission environment's inner Docker daemon is not answering"
    control, fence = _Control(RunState.PENDING, detail=why), _Fence()
    wd, rec = _gate(control, fence, [("r1", T0 - timedelta(seconds=91))])
    assert wd.tick() == 0
    assert observed_environment("r1") == ("starting", why)
    assert wd.tick() == 1
    assert rec.details["r1"].startswith(f"not ready within the readiness window — {why}")


def test_an_evidence_read_failure_never_blocks_the_close_out():
    class _NoLogs(_Control):
        def environment_logs(self, run_id: str, *, credential: str) -> str:
            raise RuntimeError("docker hiccup")

    control, fence = _NoLogs(RunState.FAILED), _Fence()
    wd, rec = _gate(control, fence, [("r1", T0)])
    assert wd.tick() == 1
    assert rec.details["r1"] == "the environment failed — the mission environment exited"


def test_closeout_detail_keeps_the_verdict_and_the_newest_evidence():
    # The error that explains a failed `compose up` is at the END of the log.
    evidence = "\n".join(f"line {i}" for i in range(3000))
    detail = closeout_detail("verdict", evidence)
    assert detail.startswith("verdict\n\n… (older lines dropped)\nline ")  # whole lines only
    assert detail.endswith("line 2999")
    assert len(detail) <= 8_000
    assert closeout_detail("verdict", "   ") == "verdict"  # nothing to attach


def test_the_default_readiness_window_is_five_minutes():
    """#43 finding 5. Measured under parallel load: layered-alibi needed 66 s and breachpoint
    >110 s just to load its images before `compose up` began — the old 90 s closed those out as
    deploy_failed while they were coming up fine. An environment that DIES is closed out at once
    regardless of the window, so the longer default costs nothing on a hard failure."""
    from xorcise.core.config import Settings

    assert Settings.model_fields["readiness_timeout_seconds"].default == 300.0


def test_a_still_starting_run_inside_its_window_is_left_alone():
    # PENDING at t+10s of a 90s window is normal startup — never terminate it.
    control, fence = _Control(RunState.PENDING), _Fence()
    wd, terminated = _watchdog(control, fence, [("r1", T0 - timedelta(seconds=10))])
    assert wd.tick() == 0
    assert terminated == []
    assert control.torn_down == []


def test_a_run_still_not_ready_past_its_window_is_terminated():
    control, fence = _Control(RunState.PENDING), _Fence()
    wd, terminated = _watchdog(control, fence, [("r1", T0 - timedelta(seconds=91))])
    # Debounced: one bad sample past the window is not enough (a probe can blip), consecutive ones
    # are. The run is closed out on the second strike.
    assert wd.tick() == 0
    assert wd.tick() == 1
    assert terminated == [("r1", "deploy_failed")]


def test_a_ready_run_is_never_terminated():
    control, fence = _Control(RunState.READY), _Fence(router_up=True)
    wd, terminated = _watchdog(control, fence, [("r1", T0 - timedelta(seconds=1000))])
    assert wd.tick() == 0
    assert terminated == []


def test_services_up_but_router_offline_is_not_ready():
    # The exact reported shape: the mission stack is up, but the router that advertises its CIDR
    # never joined, so the agent has a tailnet IP and no route. Inside the window: keep waiting.
    control, fence = _Control(RunState.READY), _Fence(router_up=False)
    wd, terminated = _watchdog(control, fence, [("r1", T0 - timedelta(seconds=10))])
    assert wd.tick() == 0
    # ...past the window it is closed out rather than left as a silent no-route run.
    wd2, terminated2 = _watchdog(control, fence, [("r1", T0 - timedelta(seconds=91))])
    wd2.tick()
    assert wd2.tick() == 1
    assert terminated2 == [("r1", "deploy_failed")]


def test_a_vanished_container_past_the_window_is_terminated():
    # NotFound (no container under the run-id name) is a dead environment too.
    control, fence = _Control(missing=True), _Fence()
    wd, terminated = _watchdog(control, fence, [("r1", T0 - timedelta(seconds=91))])
    wd.tick()
    assert wd.tick() == 1
    assert terminated == [("r1", "deploy_failed")]


def test_a_transient_blip_never_kills_a_run_that_already_came_up():
    # THE false positive to avoid. node_online_by_name degrades to False on any Headscale error, so
    # one hiccup an hour into a healthy run would otherwise satisfy "not ready + past window" and
    # terminate it. A run observed ready is never closed out by the startup window again.
    control, fence = _Control(RunState.READY), _Fence(router_up=True)
    old = T0 - timedelta(seconds=3600)
    wd, terminated = _watchdog(control, fence, [("r1", old)])
    assert wd.tick() == 0  # observed ready
    fence.router_up = False  # transient probe failure, long after the window
    assert wd.tick() == 0
    assert wd.tick() == 0
    assert terminated == []


def test_an_environment_that_dies_mid_run_is_still_closed_out():
    # The other half: once a run is ready the startup window no longer applies, but an environment
    # that actually DIES (container exited) is unambiguous and must still be closed out at once —
    # however long the run has been going.
    control, fence = _Control(RunState.READY), _Fence()
    wd, terminated = _watchdog(control, fence, [("r1", T0 - timedelta(seconds=3600))])
    assert wd.tick() == 0  # healthy
    control.state = RunState.FAILED  # the environment dies mid-run
    assert wd.tick() == 1
    assert terminated == [("r1", "deploy_failed")]


def test_one_runs_failure_does_not_abort_the_scan():
    # Isolation (mirrors reconcile): a transient error on one run must not stop the others.
    class _Boom(_Control):
        def status(self, run_id: str, *, credential: str) -> StatusResult:
            if run_id == "bad":
                raise RuntimeError("docker hiccup")
            return super().status(run_id, credential=credential)

    control, fence = _Boom(RunState.FAILED), _Fence()
    wd, terminated = _watchdog(control, fence, [("bad", T0), ("r2", T0)])
    assert wd.tick() == 1
    assert terminated == [("r2", "deploy_failed")]
