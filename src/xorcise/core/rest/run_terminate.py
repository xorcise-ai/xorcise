"""Run-termination coordinator (rest layer).

Couples the runs terminal SM with the otel trace seal and the real-graded result record —
none of which the runs module may import (dependency rule). Seal + grade + record happen
exactly once, on the first transition. make_gate builds the run-over gate injected into
the runcontrol service's gate seam: budget backstop + mission-over once terminal."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime

from xorcise.core import reporting, runs
from xorcise.core.runcontrol.errors import MissionOverError

log = logging.getLogger(__name__)

__all__ = [
    "terminate_run",
    "seal_terminal",
    "grade_and_record",
    "ensure_graded_async",
    "regrade_orphaned_terminal_runs",
    "make_gate",
    "MissionOverError",
]

# De-dup slot for concurrent grade schedulings: the terminate endpoints, /complete, the watchdog,
# the boot regrade sweep and the read-path re-drive (ensure_graded_async) can all reach
# grade_and_record for the same run — during the telemetry drain window the run is terminal and
# ungraded for seconds, so overlap is the common case, and each duplicate is a paid judge call.
# grade_and_record itself claims the slot (a latecomer no-ops) and clears it however it ends, so a
# run that is STILL ungraded can be re-driven by a later poll.
_grading_lock = threading.Lock()
_grading_in_flight: set[str] = set()


def seal_terminal(run_id: str, trigger: str, now: datetime) -> str:
    """Fast sync phase: transition immediately and begin the telemetry drain window.

    The control plane closes immediately, while OTLP remains admissible until grade_and_record
    waits the configured drain interval and seals it. A zero interval seals synchronously, which
    is useful for tests and operators who explicitly prefer the old behavior.
    """
    is_term, existing, _ = runs.terminal_state(run_id)
    if is_term:
        # Preserve the recorded value; `existing or trigger` would substitute the caller's
        # trigger if existing were an empty string.
        return existing if existing is not None else trigger
    recorded = runs.mark_terminal(run_id, trigger, now)
    if not recorded:
        return ""  # absent run — do not seal
    from xorcise.core.config import get_settings

    if get_settings().telemetry_drain_seconds == 0:
        _seal_telemetry(run_id)
    return recorded


def _seal_telemetry(run_id: str) -> None:
    """Idempotently freeze the RAW OTLP record, keeping the OTel import lazy."""
    from xorcise.core.otel.store import SqliteSealStore

    SqliteSealStore().seal(run_id)


def _drain_and_seal_telemetry(run_id: str) -> None:
    """Wait one configurable grace period, unless another finalizer already sealed the run."""
    from xorcise.core.config import get_settings
    from xorcise.core.otel.store import SqliteSealStore

    seals = SqliteSealStore()
    if seals.is_sealed(run_id):
        return
    delay = get_settings().telemetry_drain_seconds
    if delay > 0:
        time.sleep(delay)
    _seal_telemetry(run_id)


def grade_and_record(run_id: str) -> None:
    """Slow phase: drain telemetry, seal, grade, and record the result. Idempotent.

    No-op if the run is absent, not terminal, or already graded — so it is safe to schedule on a
    background thread (endpoints) AND be reached synchronously by the watchdog; record_result is
    itself idempotent on run_id as a second guard against the two racing on the same run. Claims
    the in-flight de-dup slot itself (a concurrent duplicate no-ops instead of double-paying the
    judge) and always releases it, however grading ends."""
    with _grading_lock:
        if run_id in _grading_in_flight:
            return  # a grade for this run is already in flight — the drain-window duplicate
        _grading_in_flight.add(run_id)
    try:
        _grade_run(run_id)
    finally:
        with _grading_lock:
            _grading_in_flight.discard(run_id)


def ensure_graded_async(run_id: str, schedule: Callable[[Callable[[], None]], None]) -> bool:
    """Re-drive grading for a terminal-but-ungraded run that has none in flight, then return True.

    The self-heal for a grade lost after /complete: grade_and_record runs on a fire-and-forget
    BackgroundTask, so a server restart (or hung judge call) between seal and grade leaves the run
    terminal-ungraded forever — reconcile_on_startup only converges NON-terminal runs, and nothing
    else ever re-schedules the grade, so /result 202s "grading" indefinitely. Calling this from the
    read path means the very act of polling the result drives grading forward. De-duplicated (the
    in-flight slot) so a 2-second poll loop triggers ONE grade, not a judge call per poll; safe and
    idempotent (grade_and_record no-ops once a result exists). `schedule` is the caller's
    off-thread runner (a route passes BackgroundTasks.add_task) so this layer takes no framework
    dependency and unit tests can drive it synchronously."""
    if runs.get(run_id) is None:
        return False
    if not runs.terminal_state(run_id)[0]:
        return False
    if reporting.get_result(run_id) is not None:
        return False
    # Read-only fast path: the slot itself is claimed by grade_and_record (so EVERY scheduling
    # path de-dups, not just this one). A racing poll that slips past this check schedules a
    # task that no-ops on the slot — one judge call either way.
    with _grading_lock:
        if run_id in _grading_in_flight:
            return False
    schedule(lambda: grade_and_record(run_id))
    return True


def regrade_orphaned_terminal_runs() -> int:
    """Grade every run that is terminal but never recorded a result — grades lost when the server
    stopped between seal and the background grade_and_record completing. Run once on boot (nothing
    else revisits a terminal run), so a run wedged at "grading" heals on the next start without an
    operator touching it. Idempotent + self-defending (grade_and_record records a zero fallback on
    a grading crash). Returns the count re-graded, for the boot log + tests."""
    healed = 0
    for run in runs.list_runs():
        if runs.terminal_state(run.run_id)[0] and reporting.get_result(run.run_id) is None:
            log.info("regrade: %s is terminal but ungraded (grade lost to a stop)", run.run_id)
            grade_and_record(run.run_id)
            healed += 1
    if healed:
        log.info("regrade: healed %d orphaned terminal-ungraded run(s)", healed)
    return healed


def _grade_run(run_id: str) -> None:
    run = runs.get(run_id)
    if run is None:
        return
    is_term, recorded, _ = runs.terminal_state(run_id)
    if not is_term:
        return
    if reporting.get_result(run_id) is not None:
        return  # already graded
    # The agent's /complete call can emit its tool result only after the HTTP response returns.
    # Keep OTLP open for a bounded grace period, then freeze the exact input the grader sees.
    _drain_and_seal_telemetry(run_id)
    # Lazy: grade_assembly keeps otel off the import path (plane-isolation invariant).
    # model=None → build_eval_judge reads the BYOM key from settings; returns None when
    # unconfigured so the judge half degrades cleanly.
    from xorcise.core.rest import grade_assembly

    try:
        judge = grade_assembly.build_eval_judge()
        result = judge.grade(grade_assembly.grade_request_for(run_id))
    except Exception as exc:
        # Defensive: a grading crash (e.g. a legacy installed manifest whose check op predates
        # ingest validation) must STILL record a result — otherwise /result 202s "grading"
        # forever (nothing ever re-schedules this) and the environment leaks. Mirror the judge
        # half's degrade: zero score, status + reason disclosed on the result.
        log.exception("grading failed for %s; recording a zero fallback result", run_id)
        from xorcise.core.contracts.grading import GradeResult, ScoreBreakdown

        result = GradeResult(
            run_id=run_id,
            overall=0.0,
            breakdown=ScoreBreakdown(),
            trace_ref=run_id,
            judge_status="unavailable",
            judge_detail=f"grading failed: {exc}",
        )
    from xorcise.core.config import get_settings
    from xorcise.core.contracts.reporting import ResultConditions

    _s = get_settings()
    conditions = ResultConditions(
        model=run.model,
        judge_model=_s.model_name if _s.model_configured() else None,
        budget_seconds=run.budget_seconds,
        sandbox_ref=run.sandbox_ref,
        agent_version=run.agent_version,
        install_revision=run.install_revision,
        mission_version=run.mission_version,
        mission_base_version=run.mission_base_version,
        platform=run.platform,
    )
    # a run that did not end on the agent's own terms is "partial" and must not count
    # as a genuine result against the agent — a budget "timeout" or an operator's manual kill.
    # Only the agent's own "done" completion is a full result.
    partial = recorded in ("timeout", "operator")
    # Per-run telemetry snapshot (run-report): fold the event projection once here and
    # persist it beside the grade. Agent-self-reported display data — never an observed fact, never
    # a grading input. Best-effort: a fold/projection failure must never break finalization, so it
    # logs and records the result with no snapshot (lazy imports keep the otel display plane off
    # this module's import path — plane-isolation invariant).
    stats = None
    try:
        from xorcise.core.otel.run_stats import fold_run_stats
        from xorcise.core.rest import events_view

        view = events_view._full_view(run_id)
        stats = fold_run_stats(
            view.events, created_at=run.created_at, completed_at=run.completed_at
        )
    except Exception:  # best-effort — a telemetry snapshot must never break finalization
        log.warning("run-stats fold failed for %s", run_id, exc_info=True)
    try:
        reporting.record_result(
            run_id,
            run.agent_id,
            result,
            conditions,
            partial=partial,
            partial_trigger=(recorded if partial else None),
            stats=stats,
        )
    finally:
        # release the run's environment (container + tailnet nodes) once graded — in a
        # finally so even a result-store failure cannot leak it. Idempotent + best-effort; lazy
        # import keeps the docker/headscale planes off this module's import path.
        from xorcise.core.rest.run_teardown import teardown_run

        teardown_run(run_id)
    # materialize the per-run agent-events.jsonl artifact (debug/export).
    # Best-effort — a derived, rebuildable file must never break finalization; lazy import keeps
    # the display plane off this module's import path (plane-isolation invariant).
    try:
        from xorcise.core.rest.events_export import export_run_events

        export_run_events(run_id)
    except Exception:  # best-effort — never break finalization on an export failure
        log.warning("agent-events export failed for %s", run_id, exc_info=True)
    # Attribute the terrain mission plane now the run is terminal, independent of any viewer, so
    # the map is complete for runs no one watched live (config-gated, no-op without a model).
    # Best-effort on a background thread (maybe_start_catchup_v2 owns the thread + per-run lock); a
    # display-plane concern must never break finalization. Lazy imports keep those planes off the
    # module import path (plane-isolation invariant).
    try:
        from pathlib import Path

        from xorcise.core.config import get_settings
        from xorcise.core.missions import get_installed
        from xorcise.core.rest.terrain_catchup_v2 import maybe_start_catchup_v2

        if get_settings().terrain_auto_attribute:
            installed = get_installed(run.mission, Path(get_settings().missions_root))
            manifest = installed.manifest if installed is not None else None
            maybe_start_catchup_v2(run_id, run.mission, manifest)
    except Exception:  # best-effort — never break finalization on an attribution failure
        log.warning("terrain attribution kickoff failed for %s", run_id, exc_info=True)


def terminate_run(run_id: str, trigger: str, now: datetime) -> str:
    """Synchronous composite: seal_terminal then grade_and_record. Returns the recorded trigger.

    Kept synchronous for the budget watchdog (runs in its own thread; no client waits) and for
    tests. The REST endpoints instead call seal_terminal synchronously and schedule
    grade_and_record on a BackgroundTask so the caller is not blocked by the judge."""
    is_term, existing, _ = runs.terminal_state(run_id)
    if is_term:
        return existing if existing is not None else trigger
    recorded = seal_terminal(run_id, trigger, now)
    if recorded:
        grade_and_record(run_id)
    return recorded


def make_gate(now_fn: Callable[[], datetime]) -> Callable[[str], None]:
    """Build the run-over gate for RunControlDeps.gate."""

    def gate(run_id: str) -> None:
        now = now_fn()
        if runs.is_budget_expired(run_id, now):  # backstop for the watchdog
            terminate_run(run_id, "timeout", now)
        if runs.terminal_state(run_id)[0]:
            raise MissionOverError("mission-over: this run has ended")

    return gate
